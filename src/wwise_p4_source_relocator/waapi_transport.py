from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PureWindowsPath
import socket
import ssl
import sys
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import ParseResult, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


WaapiTransport = Literal["wamp", "http"]
WaapiIssue = Literal["modal-dialog", "project-mismatch", "unreachable"]


class WaapiCallError(RuntimeError):
    def __init__(self, uri: str, message: str) -> None:
        super().__init__(message)
        self.uri = uri


@dataclass(frozen=True, slots=True)
class WaapiEndpoint:
    transport: WaapiTransport
    url: str


@dataclass(frozen=True, slots=True)
class WaapiDetection:
    endpoint: WaapiEndpoint | None
    message: str
    issue: WaapiIssue | None = None


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        return None


def _build_local_http_opener():
    return build_opener(ProxyHandler({}), _RejectRedirectHandler())


_LOCAL_HTTP_OPENER = _build_local_http_opener()


class HttpWaapiConnection:
    def __init__(self, url: str, *, timeout: float = 3.0) -> None:
        parsed = parse_local_waapi_url(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("HTTP WAAPI URL must use http:// or https://")
        self.url = url
        self.timeout = timeout

    def call(
        self,
        uri: str,
        args: dict[str, object],
        *,
        options: dict[str, object],
    ) -> dict[str, object]:
        body = json.dumps(
            {"uri": uri, "args": args, "options": options},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _LOCAL_HTTP_OPENER.open(
                request, timeout=self.timeout
            ) as response:
                payload = response.read()
        except HTTPError as exc:
            payload = exc.read()
            parsed = _json_object(payload)
            if parsed is not None:
                _raise_waapi_error(parsed, fallback_uri=uri)
            raise WaapiCallError(uri, f"HTTP WAAPI returned status {exc.code}") from exc
        except (OSError, URLError) as exc:
            raise WaapiCallError(uri, f"HTTP WAAPI connection failed: {exc}") from exc

        parsed = _json_object(payload)
        if parsed is None:
            raise WaapiCallError(uri, "HTTP WAAPI returned invalid JSON")
        _raise_waapi_error(parsed, fallback_uri=uri)
        return parsed


def detect_waapi_endpoint(
    configured_url: str,
    *,
    project_root: str | Path,
    timeout: float = 1.0,
) -> WaapiDetection:
    try:
        parsed = parse_local_waapi_url(configured_url)
    except ValueError as exc:
        return WaapiDetection(
            None,
            str(exc),
            "unreachable",
        )
    if parsed.scheme in {"http", "https"}:
        return _probe_http_endpoint(
            configured_url,
            project_root=project_root,
            timeout=timeout,
        )
    if parsed.scheme not in {"ws", "wss"}:
        return WaapiDetection(
            None,
            "WAAPI WAMP URL must use ws:// or wss://",
            "unreachable",
        )

    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/waapi"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    wamp_ready = waapi_websocket_is_reachable(
        parsed.hostname,
        port,
        path=path,
        secure=parsed.scheme == "wss",
        timeout=timeout,
    )

    http_url = _default_http_url(parsed)
    http_detection = _probe_http_endpoint(
        http_url,
        project_root=project_root,
        timeout=timeout,
    )
    if http_detection.issue in {"modal-dialog", "project-mismatch"}:
        return http_detection
    if wamp_ready:
        return WaapiDetection(
            WaapiEndpoint("wamp", configured_url),
            f"WAAPI WAMP is reachable at {configured_url}",
        )
    if http_detection.endpoint is not None:
        return http_detection
    return WaapiDetection(
        None,
        f"No Wwise WAAPI endpoint responded at {configured_url} or {http_url}",
        "unreachable",
    )


def waapi_websocket_is_reachable(
    host: str,
    port: int,
    *,
    path: str = "/waapi",
    secure: bool = False,
    timeout: float = 0.5,
) -> bool:
    """Verify that an endpoint accepts the WebSocket protocol used by WAAPI."""

    if not _is_loopback_host(host):
        return False

    normalized_path = path if path.startswith("/") else f"/{path}"
    nonce = base64.b64encode(os.urandom(16)).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1(
            (nonce + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii"),
            usedforsecurity=False,
        ).digest()
    ).decode("ascii")
    request = (
        f"GET {normalized_path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {nonce}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Protocol: wamp.2.json\r\n"
        "\r\n"
    ).encode("ascii")

    try:
        connection = socket.create_connection((host, port), timeout=timeout)
        if secure:
            context = ssl.create_default_context()
            connection = context.wrap_socket(connection, server_hostname=host)
        with connection:
            connection.sendall(request)
            response = connection.recv(8192).decode("latin-1")
    except OSError:
        return False

    lines = response.split("\r\n")
    if not lines or " 101 " not in f" {lines[0]} ":
        return False
    headers = {
        name.strip().casefold(): value.strip()
        for line in lines[1:]
        if ":" in line
        for name, value in (line.split(":", 1),)
    }
    return (
        headers.get("upgrade", "").casefold() == "websocket"
        and "upgrade" in headers.get("connection", "").casefold()
        and headers.get("sec-websocket-accept") == expected_accept
        and headers.get("sec-websocket-protocol", "").casefold() == "wamp.2.json"
    )


def _probe_http_endpoint(
    url: str,
    *,
    project_root: str | Path,
    timeout: float,
) -> WaapiDetection:
    connection = HttpWaapiConnection(url, timeout=timeout)
    try:
        connection.call("ak.wwise.core.getInfo", {}, options={})
        response = connection.call(
            "ak.wwise.core.object.get",
            {"waql": "from type project"},
            options={"return": ["name", "filePath"]},
        )
    except WaapiCallError as exc:
        if exc.uri == "ak.wwise.locked":
            return WaapiDetection(
                None,
                "Wwise is waiting for an open modal dialog to close",
                "modal-dialog",
            )
        return WaapiDetection(None, str(exc), "unreachable")

    if not _response_matches_project(response, project_root=project_root):
        return WaapiDetection(
            None,
            "The project open in Wwise does not match the selected project folder",
            "project-mismatch",
        )
    return WaapiDetection(
        WaapiEndpoint("http", url),
        f"WAAPI HTTP was detected automatically at {url}",
    )


def _default_http_url(parsed: ParseResult) -> str:
    host = parsed.hostname
    if host is None:
        raise ValueError("WAAPI URL must include a host")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunparse((scheme, f"{host}:8090", "/waapi", "", "", ""))


def parse_local_waapi_url(url: str) -> ParseResult:
    """Accept only the local WAAPI endpoints exposed by Wwise Authoring."""

    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss", "http", "https"}:
        raise ValueError(
            "WAAPI URL must use ws://, wss://, http://, or https://"
        )
    if not parsed.hostname:
        raise ValueError("WAAPI URL must include a host")
    if not _is_loopback_host(parsed.hostname):
        raise ValueError("WAAPI URL must target localhost")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "WAAPI URL port must be a number between 1 and 65535"
        ) from exc
    if port is not None and port < 1:
        raise ValueError("WAAPI URL port must be between 1 and 65535")
    return parsed


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _response_matches_project(
    response: dict[str, object], *, project_root: str | Path
) -> bool:
    records = response.get("return")
    if not isinstance(records, list) or len(records) != 1:
        return False
    record = records[0]
    if not isinstance(record, dict):
        return False
    actual = record.get("filePath")
    if not isinstance(actual, str) or not actual:
        return False
    expected_files = sorted(Path(project_root).resolve().glob("*.wproj"))
    if len(expected_files) != 1:
        return False
    actual_path = _normalize_wwise_project_path(actual)
    return actual_path is not None and actual_path == expected_files[0].resolve()


def _normalize_wwise_project_path(value: str) -> Path | None:
    if sys.platform == "win32":
        return Path(value).expanduser().resolve()
    windows_path = PureWindowsPath(value)
    if windows_path.drive:
        drive = windows_path.drive.rstrip(":").casefold()
        relative = Path(*windows_path.parts[1:])
        if drive == "z":
            return (Path("/") / relative).resolve()
        if drive == "y":
            return (Path.home() / relative).resolve()
        return None
    return Path(value.replace("\\", "/")).expanduser().resolve()


def _json_object(payload: bytes) -> dict[str, object] | None:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _raise_waapi_error(value: dict[str, object], *, fallback_uri: str) -> None:
    error_uri = value.get("uri")
    message = value.get("message")
    if isinstance(error_uri, str) and isinstance(message, str):
        raise WaapiCallError(error_uri or fallback_uri, message)

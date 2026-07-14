from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import shutil
import subprocess
from typing import Literal

from .p4_client import (
    P4CommandError,
    P4Connection,
    P4ConnectionInfo,
    p4_result_has_error,
    query_p4_connection,
)
from .waapi_transport import (
    WaapiDetection,
    WaapiEndpoint,
    detect_waapi_endpoint,
    waapi_websocket_is_reachable,
)
from .wwise_xml import WwuParseError, parse_source_references


CheckStatus = Literal["pass", "fail"]
P4WorkspaceIssue = Literal[
    "connection-unavailable",
    "not-configured",
    "not-mapped",
]


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    status: CheckStatus
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "message": self.message}


@dataclass(frozen=True, slots=True)
class PilotReadiness:
    project_root: Path
    checks: tuple[ReadinessCheck, ...]
    waapi_url: str | None = None
    waapi_transport: str | None = None
    waapi_issue: str | None = None
    p4_connection: P4ConnectionInfo | None = None
    p4_workspace_issue: P4WorkspaceIssue | None = None

    @property
    def ready(self) -> bool:
        return all(check.status == "pass" for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        connection = (
            {"url": self.waapi_url, "transport": self.waapi_transport}
            if self.waapi_url and self.waapi_transport
            else None
        )
        return {
            "projectRoot": self.project_root.as_posix(),
            "ready": self.ready,
            "checks": [check.to_dict() for check in self.checks],
            "waapiConnection": connection,
            "waapiIssue": self.waapi_issue,
            "p4Connection": (
                self.p4_connection.to_dict() if self.p4_connection else None
            ),
            "p4WorkspaceIssue": self.p4_workspace_issue,
        }


def inspect_pilot_readiness(
    project_root: str | Path,
    *,
    p4_executable: str = "p4",
    p4_connection: P4Connection | None = None,
    p4_available: bool | None = None,
    p4_connection_available: bool | None = None,
    p4_workspace: bool | None = None,
    waapi_client_available: bool | None = None,
    waapi_reachable: bool | None = None,
    waapi_host: str = "127.0.0.1",
    waapi_port: int = 8080,
    waapi_path: str = "/waapi",
    waapi_secure: bool = False,
    waapi_url: str | None = None,
) -> PilotReadiness:
    root = Path(project_root).resolve()
    checks: list[ReadinessCheck] = []

    root_exists = root.is_dir()
    checks.append(
        _check(
            "project-root",
            root_exists,
            f"Project root exists: {root}" if root_exists else f"Project root is missing: {root}",
        )
    )
    project_files = sorted(root.glob("*.wproj")) if root_exists else []
    checks.append(
        _check(
            "wwise-project",
            len(project_files) == 1,
            f"Found Wwise project: {project_files[0].name}"
            if len(project_files) == 1
            else f"Expected one .wproj file, found {len(project_files)}",
        )
    )

    originals = root / "Originals"
    wav_files = list(originals.rglob("*.wav")) if originals.is_dir() else []
    checks.append(
        _check(
            "originals-wav",
            bool(wav_files),
            f"Found {len(wav_files)} WAV source(s) under Originals"
            if wav_files
            else "No WAV sources were found under Originals",
        )
    )

    work_units = sorted(root.rglob("*.wwu")) if root_exists else []
    source_count = 0
    parse_errors = 0
    for work_unit in work_units:
        try:
            source_count += len(
                parse_source_references(work_unit, project_root=root)
            )
        except WwuParseError:
            parse_errors += 1
    checks.append(
        _check(
            "wwu-sources",
            source_count > 0 and parse_errors == 0,
            f"Found {source_count} WWU source reference(s)"
            if source_count > 0 and parse_errors == 0
            else (
                f"Found {source_count} source reference(s) with {parse_errors} parse error(s)"
            ),
        )
    )

    detected_p4 = (
        _executable_is_available(p4_executable)
        if p4_available is None
        else p4_available
    )
    checks.append(
        _check(
            "p4-cli",
            detected_p4,
            "p4 CLI is available" if detected_p4 else "p4 CLI is not available",
        )
    )
    effective_connection = p4_connection or P4Connection()
    connection_info: P4ConnectionInfo | None = None
    if p4_connection_available is None:
        if detected_p4 and root_exists:
            try:
                connection_info = query_p4_connection(
                    executable=p4_executable,
                    connection=effective_connection,
                    cwd=root,
                )
                connected = True
            except (OSError, subprocess.SubprocessError, P4CommandError):
                connected = False
        else:
            connected = False
    else:
        connected = p4_connection_available
    checks.append(
        _check(
            "p4-connection",
            connected,
            _p4_connection_message(connection_info)
            if connected
            else "Could not connect to the configured Perforce server",
        )
    )
    workspace_connection = (
        connection_info.connection if connection_info else effective_connection
    )
    workspace_issue: P4WorkspaceIssue | None = None
    if p4_workspace is not None:
        in_workspace = p4_workspace
        if not in_workspace:
            workspace_issue = "not-mapped"
    elif not detected_p4 or not connected or not root_exists:
        in_workspace = False
        workspace_issue = "connection-unavailable"
    elif not workspace_connection.client:
        in_workspace = False
        workspace_issue = "not-configured"
    else:
        in_workspace = _p4_contains_project(
            root,
            executable=p4_executable,
            connection=workspace_connection,
        )
        if not in_workspace:
            workspace_issue = "not-mapped"
    checks.append(
        _check(
            "p4-workspace",
            in_workspace,
            _p4_workspace_message(in_workspace, workspace_issue),
        )
    )

    configured_url = waapi_url or _build_waapi_url(
        host=waapi_host,
        port=waapi_port,
        path=waapi_path,
        secure=waapi_secure,
    )
    detection = (
        detect_waapi_endpoint(configured_url, project_root=root)
        if waapi_reachable is None
        else WaapiDetection(
            WaapiEndpoint("wamp", configured_url) if waapi_reachable else None,
            (
                f"WAAPI is reachable at {configured_url}"
                if waapi_reachable
                else f"WAAPI is not reachable at {configured_url}"
            ),
            None if waapi_reachable else "unreachable",
        )
    )
    client_available = (
        detection.endpoint is not None and detection.endpoint.transport == "http"
        or (
            importlib.util.find_spec("waapi") is not None
            if waapi_client_available is None
            else waapi_client_available
        )
    )
    checks.append(
        _check(
            "waapi-client",
            client_available,
            (
                "HTTP WAAPI transport is available"
                if detection.endpoint is not None
                and detection.endpoint.transport == "http"
                else "waapi-client is installed"
            )
            if client_available
            else "waapi-client is not installed",
        )
    )
    checks.append(
        _check(
            "waapi-server",
            detection.endpoint is not None,
            detection.message,
        )
    )
    endpoint = detection.endpoint
    return PilotReadiness(
        root,
        tuple(checks),
        waapi_url=endpoint.url if endpoint else None,
        waapi_transport=endpoint.transport if endpoint else None,
        waapi_issue=detection.issue,
        p4_connection=connection_info,
        p4_workspace_issue=workspace_issue,
    )


def render_readiness_markdown(readiness: PilotReadiness) -> str:
    lines = [
        "# Pilot Readiness",
        "",
        f"- Ready: {'yes' if readiness.ready else 'no'}",
        f"- Project root: `{readiness.project_root}`",
        "",
        "## Checks",
        "",
        "| Check | Status | Details |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| {check.name} | {check.status} | {_escape(check.message)} |"
        for check in readiness.checks
    )
    return "\n".join(lines) + "\n"


def _check(name: str, passed: bool, message: str) -> ReadinessCheck:
    return ReadinessCheck(name, "pass" if passed else "fail", message)


def _p4_contains_project(
    project_root: Path,
    *,
    executable: str = "p4",
    connection: P4Connection | None = None,
) -> bool:
    project_file = next(project_root.glob("*.wproj"), project_root)
    effective_connection = connection or P4Connection()
    try:
        result = subprocess.run(
            (
                executable,
                *effective_connection.global_options(),
                "where",
                str(project_file),
            ),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return (
        not p4_result_has_error(result)
        and "not in client view" not in result.stdout
    )


def _p4_connection_message(info: P4ConnectionInfo | None) -> str:
    if info is None:
        return "Perforce connection is available"
    connection = info.connection
    parts = [
        value
        for value in (
            connection.port,
            connection.user,
            connection.client,
        )
        if value
    ]
    return "Connected to Perforce: " + " · ".join(parts)


def _p4_workspace_message(
    in_workspace: bool,
    issue: P4WorkspaceIssue | None,
) -> str:
    if in_workspace:
        return "Project root is mapped in the current Perforce workspace"
    if issue == "connection-unavailable":
        return "Workspace was not checked because Perforce is not connected"
    if issue == "not-configured":
        return "No Perforce workspace is selected for the current project"
    return "Project root is not mapped in the selected Perforce workspace"


def _executable_is_available(executable: str) -> bool:
    candidate = Path(executable).expanduser()
    if candidate.parent != Path("."):
        return candidate.is_file()
    return shutil.which(executable) is not None


def _build_waapi_url(
    *, host: str, port: int, path: str, secure: bool
) -> str:
    scheme = "wss" if secure else "ws"
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{scheme}://{host}:{port}{normalized_path}"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")

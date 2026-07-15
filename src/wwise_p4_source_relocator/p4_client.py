from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
from typing import Mapping


LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())

DEFAULT_P4_TIMEOUT = 30.0


def p4_creation_flags(os_name: str | None = None) -> int:
    if (os_name or os.name) != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def run_p4_process(
    argv: tuple[str, ...],
    *,
    cwd: str | Path | None = None,
    timeout: float = DEFAULT_P4_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        creationflags=p4_creation_flags(),
    )


class P4ExecutionDisabled(RuntimeError):
    """Raised when code attempts to execute through a dry-run client."""


class P4CommandError(subprocess.CalledProcessError):
    """Raised when p4 reports an error even if its process exits successfully."""


@dataclass(frozen=True, slots=True)
class P4Command:
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class P4Connection:
    """Non-secret Perforce connection settings shared with P4V."""

    port: str | None = None
    user: str | None = None
    client: str | None = None
    charset: str | None = None

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> P4Connection:
        values = environment if environment is not None else os.environ
        return cls(
            port=_optional(values.get("P4PORT")),
            user=_optional(values.get("P4USER")),
            client=_optional(values.get("P4CLIENT")),
            charset=_optional(values.get("P4CHARSET")),
        )

    @property
    def configured(self) -> bool:
        return any((self.port, self.user, self.client, self.charset))

    def global_options(self) -> tuple[str, ...]:
        options: list[str] = []
        for flag, value in (
            ("-p", self.port),
            ("-u", self.user),
            ("-c", self.client),
            ("-C", self.charset),
        ):
            if value:
                options.extend((flag, value))
        return tuple(options)

    def to_dict(self) -> dict[str, str]:
        return {
            "port": self.port or "",
            "user": self.user or "",
            "client": self.client or "",
            "charset": self.charset or "",
        }


@dataclass(frozen=True, slots=True)
class P4ConnectionInfo:
    connection: P4Connection
    server_version: str | None = None
    server_address: str | None = None
    client_candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            **self.connection.to_dict(),
            "serverVersion": self.server_version or "",
            "serverAddress": self.server_address or "",
            "clientCandidates": list(self.client_candidates),
        }


class P4Client:
    """Small argv-safe p4 wrapper that defaults to construction-only mode."""

    def __init__(
        self,
        *,
        executable: str = "p4",
        connection: P4Connection | None = None,
        dry_run: bool = True,
        timeout: float = DEFAULT_P4_TIMEOUT,
    ) -> None:
        self.executable = executable
        self.connection = connection or P4Connection()
        self.dry_run = dry_run
        self.timeout = timeout

    def command(self, operation: str, *args: str | Path) -> P4Command:
        return P4Command(
            (
                self.executable,
                *self.connection.global_options(),
                operation,
                *(str(arg) for arg in args),
            )
        )

    def opened(self, *paths: str | Path) -> P4Command:
        return self.command("opened", *paths)

    def where(self, path: str | Path) -> P4Command:
        return self.command("where", path)

    def edit(self, path: str | Path, *, changelist: str | None = None) -> P4Command:
        args: list[str | Path] = []
        if changelist is not None:
            args.extend(("-c", changelist))
        args.append(path)
        return self.command("edit", *args)

    def move(
        self,
        source: str | Path,
        target: str | Path,
        *,
        changelist: str | None = None,
    ) -> P4Command:
        args: list[str | Path] = []
        if changelist is not None:
            args.extend(("-c", changelist))
        args.extend((source, target))
        return self.command("move", *args)

    def diff(self, path: str | Path) -> P4Command:
        return self.command("diff", "-du", path)

    def revert(
        self, *paths: str | Path, changelist: str | None = None
    ) -> P4Command:
        args: list[str | Path] = []
        if changelist is not None:
            args.extend(("-c", changelist))
        args.extend(paths)
        return self.command("revert", *args)

    def run(self, command: P4Command) -> subprocess.CompletedProcess[str]:
        if self.dry_run:
            raise P4ExecutionDisabled(
                "p4 execution is disabled; construct and inspect commands only"
            )
        argv = (command.argv[0], "-s", *command.argv[1:])
        result = run_p4_process(argv, timeout=self.timeout)
        status_output = f"{result.stdout}\n{result.stderr}"
        has_reported_error = _output_has_error(status_output)
        if result.returncode != 0 or has_reported_error:
            raise P4CommandError(
                result.returncode or 1,
                argv,
                output=result.stdout,
                stderr=result.stderr,
            )
        return subprocess.CompletedProcess(
            command.argv,
            result.returncode,
            stdout=_strip_status_prefixes(result.stdout),
            stderr=result.stderr,
        )


def query_p4_connection(
    *,
    executable: str = "p4",
    connection: P4Connection | None = None,
    cwd: str | Path | None = None,
    timeout: float = 5.0,
) -> P4ConnectionInfo:
    """Resolve the effective server, user, and client without storing credentials."""

    requested = connection or P4Connection()
    argv = (
        executable,
        *requested.global_options(),
        "-ztag",
        "info",
    )
    result = run_p4_process(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        timeout=timeout,
    )
    if result.returncode != 0 or _output_has_error(
        f"{result.stdout}\n{result.stderr}"
    ):
        _log_p4_failure("info", result)
        raise P4CommandError(
            result.returncode or 1,
            argv,
            output=result.stdout,
            stderr=result.stderr,
        )
    values = _parse_ztag(result.stdout)
    local_settings = _query_local_p4_settings(
        executable=executable,
        cwd=cwd,
        timeout=timeout,
    )
    resolved = P4Connection(
        port=requested.port or local_settings.port,
        user=(
            requested.user
            or local_settings.user
            or _optional(values.get("userName"))
        ),
        client=(
            requested.client
            or local_settings.client
            or _known_client(values.get("clientName"))
        ),
        charset=requested.charset or local_settings.charset,
    )
    client_candidates: tuple[str, ...] = ()
    if resolved.client is None and cwd is not None and resolved.user:
        client_candidates = _matching_project_clients(
            executable=executable,
            connection=resolved,
            project_path=Path(cwd),
            timeout=timeout,
        )
        if len(client_candidates) == 1:
            resolved = P4Connection(
                port=resolved.port,
                user=resolved.user,
                client=client_candidates[0],
                charset=resolved.charset,
            )
    if not resolved.user:
        raise P4CommandError(
            1,
            argv,
            output=result.stdout,
            stderr="Perforce info did not identify a user",
        )
    return P4ConnectionInfo(
        connection=resolved,
        server_version=_optional(values.get("serverVersion")),
        server_address=_optional(values.get("serverAddress")),
        client_candidates=client_candidates,
    )


def _query_local_p4_settings(
    *,
    executable: str,
    cwd: str | Path | None,
    timeout: float,
) -> P4Connection:
    """Read non-secret P4 settings without treating serverAddress as P4PORT."""

    try:
        result = run_p4_process(
            (executable, "set"),
            cwd=str(cwd) if cwd is not None else None,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return P4Connection()
    if result.returncode != 0 or _output_has_error(
        f"{result.stdout}\n{result.stderr}"
    ):
        _log_p4_failure("set", result)
        return P4Connection()
    values = _parse_p4_set(result.stdout)
    return P4Connection(
        port=values.get("P4PORT"),
        user=values.get("P4USER"),
        client=values.get("P4CLIENT"),
        charset=values.get("P4CHARSET"),
    )


def _matching_project_clients(
    *,
    executable: str,
    connection: P4Connection,
    project_path: Path,
    timeout: float,
) -> tuple[str, ...]:
    if not connection.user:
        return ()
    try:
        result = run_p4_process(
            (
                executable,
                *connection.global_options(),
                "-ztag",
                "clients",
                "-u",
                connection.user,
            ),
            cwd=str(project_path),
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0 or _output_has_error(
        f"{result.stdout}\n{result.stderr}"
    ):
        _log_p4_failure("clients", result)
        return ()

    local_host = socket.gethostname().casefold()
    client_names: list[str] = []
    for record in _parse_ztag_records(result.stdout, record_key="client"):
        name = _optional(record.get("client"))
        host = _optional(record.get("Host"))
        if name and (not host or host.casefold() == local_host):
            client_names.append(name)

    mapping_path = next(project_path.glob("*.wproj"), project_path)
    matches: list[str] = []
    for client in client_names[:25]:
        candidate = P4Connection(
            port=connection.port,
            user=connection.user,
            client=client,
            charset=connection.charset,
        )
        try:
            spec = run_p4_process(
                (
                    executable,
                    *candidate.global_options(),
                    "-ztag",
                    "client",
                    "-o",
                    client,
                ),
                cwd=str(project_path),
                timeout=timeout,
            )
            if p4_result_has_error(spec):
                continue
            spec_values = _parse_ztag(spec.stdout)
            roots = [
                value
                for key, value in spec_values.items()
                if key == "Root" or key.startswith("AltRoots")
            ]
            if not any(
                _path_is_within(mapping_path, Path(root)) for root in roots
            ):
                continue
            where = run_p4_process(
                (
                    executable,
                    *candidate.global_options(),
                    "where",
                    str(mapping_path),
                ),
                cwd=str(project_path),
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if (
            not p4_result_has_error(where)
            and "not in client view" not in where.stdout
        ):
            matches.append(client)
    return tuple(matches)


def _strip_status_prefixes(output: str) -> str:
    lines: list[str] = []
    for line in output.splitlines(keepends=True):
        if line.startswith("exit: "):
            continue
        lines.append(re.sub(r"^(?:info\d*|text): ", "", line))
    return "".join(lines)


def _parse_ztag(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(r"^\.\.\.\s+(\S+)\s*(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def _parse_ztag_records(
    output: str, *, record_key: str
) -> tuple[dict[str, str], ...]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(r"^\.\.\.\s+(\S+)\s*(.*)$", line)
        if not match:
            continue
        key = match.group(1)
        if key == record_key and record_key in current:
            records.append(current)
            current = {}
        current[key] = match.group(2).strip()
    if current:
        records.append(current)
    return tuple(records)


def _parse_p4_set(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(
            r"^(P4PORT|P4USER|P4CLIENT|P4CHARSET)=([^\s]+)",
            line.strip(),
        )
        if match:
            values[match.group(1)] = match.group(2)
    return values


def p4_result_has_error(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode != 0 or _output_has_error(
        f"{result.stdout}\n{result.stderr}"
    )


def _output_has_error(output: str) -> bool:
    return any(
        line.strip().casefold().startswith(
            ("error:", "fatal:", "perforce client error:")
        )
        for line in output.splitlines()
    )


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path_value = os.path.normcase(os.path.abspath(str(path)))
        root_value = os.path.normcase(os.path.abspath(str(root)))
        return os.path.commonpath((path_value, root_value)) == root_value
    except ValueError:
        return False


def _log_p4_failure(
    operation: str, result: subprocess.CompletedProcess[str]
) -> None:
    details = "\n".join(
        line.strip()
        for line in f"{result.stdout}\n{result.stderr}".splitlines()
        if line.strip()
    )
    LOGGER.warning(
        "Perforce %s failed with exit code %s: %s",
        operation,
        result.returncode,
        details[:2000] or "no output",
    )


def _known_client(value: str | None) -> str | None:
    normalized = _optional(value)
    if normalized is None or normalized.casefold() in {"*unknown*", "unknown"}:
        return None
    return normalized


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None

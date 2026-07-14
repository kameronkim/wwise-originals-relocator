from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from typing import Mapping


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

    def to_dict(self) -> dict[str, object]:
        return {
            **self.connection.to_dict(),
            "serverVersion": self.server_version or "",
        }


class P4Client:
    """Small argv-safe p4 wrapper that defaults to construction-only mode."""

    def __init__(
        self,
        *,
        executable: str = "p4",
        connection: P4Connection | None = None,
        dry_run: bool = True,
    ) -> None:
        self.executable = executable
        self.connection = connection or P4Connection()
        self.dry_run = dry_run

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
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
        )
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
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0 or _output_has_error(
        f"{result.stdout}\n{result.stderr}"
    ):
        raise P4CommandError(
            result.returncode or 1,
            argv,
            output=result.stdout,
            stderr=result.stderr,
        )
    values = _parse_ztag(result.stdout)
    resolved = P4Connection(
        port=requested.port or _optional(values.get("serverAddress")),
        user=requested.user or _optional(values.get("userName")),
        client=requested.client or _known_client(values.get("clientName")),
        charset=requested.charset,
    )
    if not resolved.port or not resolved.user:
        raise P4CommandError(
            1,
            argv,
            output=result.stdout,
            stderr="Perforce info did not identify a server and user",
        )
    return P4ConnectionInfo(
        connection=resolved,
        server_version=_optional(values.get("serverVersion")),
    )


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

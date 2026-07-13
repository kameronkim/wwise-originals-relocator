from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


class P4ExecutionDisabled(RuntimeError):
    """Raised when code attempts to execute through a dry-run client."""


class P4CommandError(subprocess.CalledProcessError):
    """Raised when p4 reports an error even if its process exits successfully."""


@dataclass(frozen=True, slots=True)
class P4Command:
    argv: tuple[str, ...]


class P4Client:
    """Small argv-safe p4 wrapper that defaults to construction-only mode."""

    def __init__(self, *, executable: str = "p4", dry_run: bool = True) -> None:
        self.executable = executable
        self.dry_run = dry_run

    def command(self, operation: str, *args: str | Path) -> P4Command:
        return P4Command((self.executable, operation, *(str(arg) for arg in args)))

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
        has_reported_error = any(
            line.startswith(("error:", "fatal:"))
            for line in status_output.splitlines()
        )
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


def _strip_status_prefixes(output: str) -> str:
    lines: list[str] = []
    for line in output.splitlines(keepends=True):
        if line.startswith("exit: "):
            continue
        lines.append(re.sub(r"^(?:info\d*|text): ", "", line))
    return "".join(lines)

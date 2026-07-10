from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Sequence


class P4ExecutionDisabled(RuntimeError):
    """Raised when code attempts to execute through a dry-run client."""


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

    def run(self, command: P4Command) -> subprocess.CompletedProcess[str]:
        if self.dry_run:
            raise P4ExecutionDisabled(
                "p4 execution is disabled; construct and inspect commands only"
            )
        return subprocess.run(
            command.argv,
            check=True,
            capture_output=True,
            text=True,
        )

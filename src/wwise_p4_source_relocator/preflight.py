from __future__ import annotations

from collections.abc import Iterable, Sequence
import os
from pathlib import Path
import re
import shutil
import subprocess
from time import perf_counter
from typing import Protocol

from .models import RelocationPlan, ValidationIssue, ValidationResult
from .p4_client import P4Connection, p4_result_has_error, run_p4_process
from .project_paths import UnsafeProjectPath, resolve_project_path


class WorkspaceProbe(Protocol):
    def is_available(self) -> bool: ...

    def is_in_workspace(self, path: Path) -> bool: ...

    def is_opened(self, path: Path) -> bool: ...

    def has_local_changes(self, path: Path) -> bool: ...


class P4WorkspaceProbe:
    batch_size = 32

    def __init__(
        self,
        executable: str = "p4",
        connection: P4Connection | None = None,
    ) -> None:
        self.executable = executable
        self.connection = connection or P4Connection()
        self.command_count = 0
        self.elapsed_seconds = 0.0
        self._workspace_cache: dict[str, bool] = {}
        self._opened_cache: dict[str, bool] = {}
        self._local_change_cache: dict[str, bool] = {}
        self._depot_by_path: dict[str, str] = {}
        self._client_by_path: dict[str, str] = {}

    def is_available(self) -> bool:
        return shutil.which(self.executable) is not None

    def is_in_workspace(self, path: Path) -> bool:
        key = _path_key(path)
        if key in self._workspace_cache:
            return self._workspace_cache[key]
        result = self._run("where", path)
        mapped = (
            not p4_result_has_error(result)
            and "not in client view" not in result.stdout
        )
        self._workspace_cache[key] = mapped
        return mapped

    def is_opened(self, path: Path) -> bool:
        key = _path_key(path)
        if key in self._opened_cache:
            return self._opened_cache[key]
        result = self._run("opened", path)
        output = result.stdout.strip()
        opened = (
            not p4_result_has_error(result)
            and bool(output)
            and "not opened" not in output.casefold()
        )
        self._opened_cache[key] = opened
        return opened

    def has_local_changes(self, path: Path) -> bool:
        key = _path_key(path)
        if key in self._local_change_cache:
            return self._local_change_cache[key]
        result = self._run("diff", "-se", path)
        changed = not p4_result_has_error(result) and bool(result.stdout.strip())
        self._local_change_cache[key] = changed
        return changed

    def prefetch(
        self,
        *,
        workspace_paths: Sequence[Path],
        opened_paths: Sequence[Path],
        local_change_paths: Sequence[Path],
    ) -> None:
        """Populate path states with chunked read-only Perforce calls."""

        workspace = _unique_paths(workspace_paths)
        opened = _unique_paths(opened_paths)
        local_changes = _unique_paths(local_change_paths)
        self._prefetch_workspace(workspace)
        self._prefetch_depot_state(
            opened,
            operation="opened",
            cache=self._opened_cache,
        )
        for path in local_changes:
            self.has_local_changes(path)

    def metrics(self) -> dict[str, int | float]:
        return {
            "commandCount": self.command_count,
            "elapsedMs": round(self.elapsed_seconds * 1000, 3),
            "batchSize": self.batch_size,
        }

    def _prefetch_workspace(self, paths: tuple[Path, ...]) -> None:
        for chunk in _chunks(paths, self.batch_size):
            result = self._run_tagged("where", *chunk)
            mapped: dict[str, str] = {}
            for record in _tagged_records(result.stdout):
                local_path = record.get("path")
                depot_path = record.get("depotFile")
                if (
                    not local_path
                    or not depot_path
                    or depot_path.startswith("-")
                    or "unmap" in record
                ):
                    continue
                mapped[_path_key(Path(local_path))] = _p4_identifier(depot_path)
            for path in chunk:
                key = _path_key(path)
                depot_path = mapped.get(key)
                self._workspace_cache[key] = depot_path is not None
                if depot_path is not None:
                    self._depot_by_path[key] = depot_path
            for record in _tagged_records(result.stdout):
                local_path = record.get("path")
                client_path = record.get("clientFile")
                depot_path = record.get("depotFile", "")
                if (
                    local_path
                    and client_path
                    and not depot_path.startswith("-")
                    and "unmap" not in record
                ):
                    self._client_by_path[_path_key(Path(local_path))] = (
                        _p4_identifier(client_path)
                    )

    def _prefetch_depot_state(
        self,
        paths: tuple[Path, ...],
        *,
        operation: str,
        cache: dict[str, bool],
    ) -> None:
        for chunk in _chunks(paths, self.batch_size):
            result = self._run_tagged(operation, *chunk)
            matching_identifiers = {
                _p4_identifier(value)
                for record in _tagged_records(result.stdout)
                for key in ("depotFile", "clientFile", "path")
                if (value := record.get(key))
            }
            for path in chunk:
                key = _path_key(path)
                expected = {
                    value
                    for value in (
                        self._depot_by_path.get(key),
                        self._client_by_path.get(key),
                        _p4_identifier(str(path)),
                    )
                    if value
                }
                cache[key] = bool(
                    expected.intersection(matching_identifiers)
                )

    def _run(
        self, operation: str, *args: str | Path
    ) -> subprocess.CompletedProcess[str]:
        return self._run_argv(
            operation,
            *(str(arg) for arg in args),
        )

    def _run_tagged(
        self, operation: str, *args: str | Path
    ) -> subprocess.CompletedProcess[str]:
        return self._run_argv(
            "-ztag",
            operation,
            *(str(arg) for arg in args),
        )

    def _run_argv(self, *args: str) -> subprocess.CompletedProcess[str]:
        started = perf_counter()
        try:
            return run_p4_process(
                (
                    self.executable,
                    *self.connection.global_options(),
                    *args,
                )
            )
        finally:
            self.command_count += 1
            self.elapsed_seconds += perf_counter() - started


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    unique: dict[str, Path] = {}
    for path in paths:
        unique.setdefault(_path_key(path), path)
    return tuple(unique.values())


def _chunks(
    paths: tuple[Path, ...], size: int
) -> Iterable[tuple[Path, ...]]:
    for index in range(0, len(paths), size):
        yield paths[index : index + size]


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path))).casefold()


def _p4_identifier(value: str) -> str:
    if value.startswith(("//", "-//")):
        return value.replace("\\", "/").casefold()
    return _path_key(Path(value))


def _tagged_records(output: str) -> tuple[dict[str, str], ...]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(r"^\.\.\.\s+(\S+)\s*(.*)$", line)
        if not match:
            continue
        key = match.group(1)
        if key == "depotFile" and "depotFile" in current:
            records.append(current)
            current = {}
        current[key] = match.group(2).strip()
    if current:
        records.append(current)
    return tuple(records)


def validate_relocation_plan(
    plan: RelocationPlan, *, probe: WorkspaceProbe | None = None
) -> ValidationResult:
    workspace = probe or P4WorkspaceProbe()
    issues: list[ValidationIssue] = []
    project_root = plan.project_root.resolve()

    if not project_root.is_dir():
        issues.append(
            ValidationIssue(
                "project-root-missing", f"Project root is missing: {project_root}"
            )
        )
    if not (project_root / "Originals").is_dir():
        issues.append(
            ValidationIssue(
                "originals-missing",
                f"Originals folder is missing: {project_root / 'Originals'}",
            )
        )

    p4_available = workspace.is_available()
    if not p4_available:
        issues.append(ValidationIssue("p4-unavailable", "p4 CLI is not available"))

    workspace_membership: dict[str, bool] = {}
    opened_state: dict[str, bool] = {}
    local_change_state: dict[str, bool] = {}

    def path_key(path: Path) -> str:
        return _path_key(path)

    def is_in_workspace(path: Path) -> bool:
        key = path_key(path)
        if key not in workspace_membership:
            workspace_membership[key] = workspace.is_in_workspace(path)
        return workspace_membership[key]

    def is_opened(path: Path) -> bool:
        key = path_key(path)
        if key not in opened_state:
            opened_state[key] = workspace.is_opened(path)
        return opened_state[key]

    def has_local_changes(path: Path) -> bool:
        key = path_key(path)
        if key not in local_change_state:
            local_change_state[key] = workspace.has_local_changes(path)
        return local_change_state[key]

    if p4_available:
        workspace_paths: list[Path] = []
        opened_paths: list[Path] = []
        local_change_paths: list[Path] = []
        for item in plan.items:
            if (
                item.action != "move-and-patch"
                or not item.from_relative_path
                or not item.to_relative_path
            ):
                continue
            try:
                source = resolve_project_path(
                    project_root, item.from_relative_path
                )
                target = resolve_project_path(
                    project_root, item.to_relative_path
                )
                work_unit = resolve_project_path(
                    project_root, item.work_unit_path
                )
            except UnsafeProjectPath:
                continue
            workspace_paths.extend((source, target, work_unit))
            opened_paths.extend((source, work_unit))
            local_change_paths.append(work_unit)
        prefetch = getattr(workspace, "prefetch", None)
        if callable(prefetch):
            prefetch(
                workspace_paths=tuple(workspace_paths),
                opened_paths=tuple(opened_paths),
                local_change_paths=tuple(local_change_paths),
            )

    for item in plan.items:
        if item.action == "manual-review":
            issues.append(
                ValidationIssue(
                    "manual-review",
                    item.reason or "Plan item requires manual review",
                    item.object_path,
                )
            )
            continue
        if item.action == "skip":
            continue
        if not item.from_relative_path or not item.to_relative_path:
            issues.append(
                ValidationIssue(
                    "incomplete-move",
                    "Move item is missing a source or target path",
                    item.object_path,
                )
            )
            continue

        try:
            source = resolve_project_path(project_root, item.from_relative_path)
            target = resolve_project_path(project_root, item.to_relative_path)
            work_unit = resolve_project_path(project_root, item.work_unit_path)
        except UnsafeProjectPath as exc:
            issues.append(
                ValidationIssue("outside-project", str(exc), item.object_path)
            )
            continue
        if source.resolve() == target.resolve():
            issues.append(
                ValidationIssue(
                    "same-path", "Source and target paths are the same", item.object_path
                )
            )
        if not source.is_file():
            issues.append(
                ValidationIssue(
                    "source-missing", f"Source WAV is missing: {source}", item.object_path
                )
            )
        if target.exists():
            issues.append(
                ValidationIssue(
                    "target-exists", f"Target WAV already exists: {target}", item.object_path
                )
            )
        if not work_unit.is_file():
            issues.append(
                ValidationIssue(
                    "work-unit-missing",
                    f"Work Unit is missing: {work_unit}",
                    item.object_path,
                )
            )

        if p4_available:
            workspace_paths = (
                (source, "Source WAV"),
                (target, "Target WAV"),
                (work_unit, "Work Unit"),
            )
            for path, label in workspace_paths:
                if not is_in_workspace(path):
                    issues.append(
                        ValidationIssue(
                            "outside-workspace",
                            f"{label} is outside the Perforce workspace: {path}",
                            item.object_path,
                        )
                    )
            for path, label in ((source, "Source WAV"), (work_unit, "Work Unit")):
                if is_opened(path):
                    issues.append(
                        ValidationIssue(
                            "already-opened",
                            f"{label} is already opened in Perforce: {path}",
                            item.object_path,
                        )
                    )
            if has_local_changes(work_unit):
                issues.append(
                    ValidationIssue(
                        "work-unit-local-changes",
                        "Work Unit has existing local changes outside this "
                        f"operation: {work_unit}",
                        item.object_path,
                    )
                )

    return ValidationResult(tuple(issues))

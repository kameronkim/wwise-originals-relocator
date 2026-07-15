from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
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
    def __init__(
        self,
        executable: str = "p4",
        connection: P4Connection | None = None,
    ) -> None:
        self.executable = executable
        self.connection = connection or P4Connection()

    def is_available(self) -> bool:
        return shutil.which(self.executable) is not None

    def is_in_workspace(self, path: Path) -> bool:
        result = self._run("where", path)
        return (
            not p4_result_has_error(result)
            and "not in client view" not in result.stdout
        )

    def is_opened(self, path: Path) -> bool:
        result = self._run("opened", path)
        output = result.stdout.strip()
        return (
            not p4_result_has_error(result)
            and bool(output)
            and "not opened" not in output.casefold()
        )

    def has_local_changes(self, path: Path) -> bool:
        result = self._run("diff", "-se", path)
        return not p4_result_has_error(result) and bool(result.stdout.strip())

    def _run(
        self, operation: str, *args: str | Path
    ) -> subprocess.CompletedProcess[str]:
        return run_p4_process(
            (
                self.executable,
                *self.connection.global_options(),
                operation,
                *(str(arg) for arg in args),
            )
        )


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
        return str(path.resolve()).casefold()

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

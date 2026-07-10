from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

from .models import RollbackManifest, ValidationIssue, ValidationResult
from .p4_client import P4Client
from .project_paths import UnsafeProjectPath, resolve_project_path
from .report import write_json_document


def rollback_manifest(
    manifest: RollbackManifest,
    *,
    p4: P4Client,
    manifest_path: str | Path | None = None,
) -> ValidationResult:
    root = manifest.project_root.resolve()
    issues: list[ValidationIssue] = []

    if (
        len(manifest.moves) != 1
        or len(manifest.patched_files) != 1
        or len(manifest.affected_objects) != 1
    ):
        return _record_failed_manifest(
            manifest,
            manifest_path,
            ValidationIssue(
                "manifest-scope",
                "Rollback manifest must describe exactly one move, patch, and object",
            ),
        )
    allowed_unmanaged = {manifest.moves[0].to_relative_path}
    if not set(manifest.unmanaged_files_to_delete).issubset(allowed_unmanaged):
        return _record_failed_manifest(
            manifest,
            manifest_path,
            ValidationIssue(
                "manifest-scope",
                "Unmanaged cleanup paths must match the recorded move target",
            ),
        )

    try:
        resolved_moves = [
            (
                resolve_project_path(root, move.from_relative_path),
                resolve_project_path(root, move.to_relative_path),
            )
            for move in manifest.moves
        ]
        resolved_work_units = [
            resolve_project_path(root, patched.relative_path)
            for patched in manifest.patched_files
        ]
        unmanaged_files = [
            resolve_project_path(root, relative_path)
            for relative_path in manifest.unmanaged_files_to_delete
        ]
    except UnsafeProjectPath as exc:
        return _record_failed_manifest(
            manifest,
            manifest_path,
            ValidationIssue("outside-project", str(exc)),
        )

    for source, target in resolved_moves:
        try:
            p4.run(p4.revert(source, target, changelist=manifest.changelist))
        except (OSError, subprocess.CalledProcessError) as exc:
            issues.append(ValidationIssue("p4-revert-move-failed", str(exc)))

    for work_unit in resolved_work_units:
        try:
            p4.run(p4.revert(work_unit, changelist=manifest.changelist))
        except (OSError, subprocess.CalledProcessError) as exc:
            issues.append(ValidationIssue("p4-revert-wwu-failed", str(exc)))

    for path in unmanaged_files:
        if path.is_file():
            try:
                path.unlink()
            except OSError as exc:
                issues.append(
                    ValidationIssue("unmanaged-delete-failed", str(exc))
                )

    for source, target in resolved_moves:
        if not source.is_file():
            issues.append(
                ValidationIssue(
                    "rollback-source-missing", f"Source WAV was not restored: {source}"
                )
            )
        if target.exists():
            issues.append(
                ValidationIssue(
                    "rollback-target-present", f"Target WAV remains after rollback: {target}"
                )
            )

    for patched, work_unit in zip(manifest.patched_files, resolved_work_units):
        if not work_unit.is_file():
            issues.append(
                ValidationIssue(
                    "rollback-wwu-missing", f"Work Unit was not restored: {work_unit}"
                )
            )
            continue
        digest = hashlib.sha256(work_unit.read_bytes()).hexdigest()
        if digest != patched.original_sha256:
            issues.append(
                ValidationIssue(
                    "rollback-wwu-mismatch",
                    f"Work Unit does not match its original hash: {work_unit}",
                )
            )

    result = ValidationResult(tuple(issues))
    if manifest_path is not None:
        status = "rolled-back" if result.is_valid else "failed"
        write_json_document(manifest.with_status(status), manifest_path)
    return result


def _record_failed_manifest(
    manifest: RollbackManifest,
    manifest_path: str | Path | None,
    issue: ValidationIssue,
) -> ValidationResult:
    if manifest_path is not None:
        write_json_document(manifest.with_status("failed"), manifest_path)
    return ValidationResult((issue,))

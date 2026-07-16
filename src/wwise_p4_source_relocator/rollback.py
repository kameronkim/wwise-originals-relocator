from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

from .file_ops import move_file_no_replace
from .models import RollbackManifest, ValidationIssue, ValidationResult
from .operation_lock import ProjectOperationBusyError, project_operation_lock
from .p4_client import P4Client
from .project_paths import UnsafeProjectPath, resolve_project_path
from .report import write_json_document
from .wwise_xml import (
    PreparedWwuPatch,
    WwuPatchError,
    prepare_source_path_patches,
    write_prepared_patch,
)


def rollback_manifest(
    manifest: RollbackManifest,
    *,
    p4: P4Client,
    manifest_path: str | Path | None = None,
) -> ValidationResult:
    try:
        with project_operation_lock(manifest.project_root):
            return _rollback_manifest_unlocked(
                manifest,
                p4=p4,
                manifest_path=manifest_path,
            )
    except ProjectOperationBusyError as exc:
        return ValidationResult(
            (ValidationIssue("project-operation-busy", str(exc)),)
        )


def _rollback_manifest_unlocked(
    manifest: RollbackManifest,
    *,
    p4: P4Client,
    manifest_path: str | Path | None = None,
) -> ValidationResult:
    root = manifest.project_root.resolve()
    issues: list[ValidationIssue] = []

    if (
        not manifest.moves
        or len(manifest.moves) != len(manifest.patched_files)
        or len(manifest.moves) != len(manifest.affected_objects)
    ):
        return _record_failed_manifest(
            manifest,
            manifest_path,
            ValidationIssue(
                "manifest-scope",
                "Rollback manifest must describe matching moves, patches, and objects",
            ),
        )
    allowed_unmanaged = {move.to_relative_path for move in manifest.moves}
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
        resolved_work_units = {
            patched.relative_path: resolve_project_path(root, patched.relative_path)
            for patched in manifest.patched_files
        }
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

    for source, target in reversed(resolved_moves):
        try:
            p4.run(p4.revert(source, target, changelist=manifest.changelist))
        except (OSError, subprocess.CalledProcessError) as exc:
            issues.append(ValidationIssue("p4-revert-move-failed", str(exc)))

    for work_unit in resolved_work_units.values():
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

    checked_work_units: set[str] = set()
    for patched in manifest.patched_files:
        if patched.relative_path in checked_work_units:
            continue
        checked_work_units.add(patched.relative_path)
        work_unit = resolved_work_units[patched.relative_path]
        if not work_unit.is_file():
            issues.append(
                ValidationIssue(
                    "rollback-wwu-missing", f"Work Unit was not restored: {work_unit}"
                )
            )
            continue
        digest = hashlib.sha256(work_unit.read_bytes()).hexdigest()
        expected_hashes = {
            record.original_sha256
            for record in manifest.patched_files
            if record.relative_path == patched.relative_path
        }
        if len(expected_hashes) != 1 or digest not in expected_hashes:
            expected = ", ".join(sorted(expected_hashes)) or "none"
            issues.append(
                ValidationIssue(
                    "rollback-wwu-mismatch",
                    "Work Unit does not match its original hash: "
                    f"{work_unit}; expected={expected}; actual={digest}",
                )
            )

    result = ValidationResult(tuple(issues))
    if manifest_path is not None:
        status = "rolled-back" if result.is_valid else "failed"
        write_json_document(manifest.with_status(status), manifest_path)
    return result


def rollback_local_manifest(
    manifest: RollbackManifest,
    *,
    manifest_path: str | Path | None = None,
) -> ValidationResult:
    """Reverse a local apply under the project-scoped operation lock."""

    try:
        with project_operation_lock(manifest.project_root):
            return _rollback_local_manifest_unlocked(
                manifest,
                manifest_path=manifest_path,
            )
    except ProjectOperationBusyError as exc:
        return ValidationResult(
            (ValidationIssue("project-operation-busy", str(exc)),)
        )


def _rollback_local_manifest_unlocked(
    manifest: RollbackManifest,
    *,
    manifest_path: str | Path | None = None,
) -> ValidationResult:
    """Reverse a local-filesystem apply after proving every file is unchanged.

    Both the applied and already-restored state are accepted for each recorded
    path. This makes a retry safe after an interrupted rollback without ever
    overwriting an unrecorded WAV or Work Unit edit.
    """

    if manifest.operation_mode != "local-filesystem":
        return _record_failed_manifest(
            manifest,
            manifest_path,
            ValidationIssue(
                "operation-mode",
                "Local rollback requires a local-filesystem manifest",
            ),
        )
    if (
        not manifest.moves
        or len(manifest.moves) != len(manifest.patched_files)
        or len(manifest.moves) != len(manifest.affected_objects)
    ):
        return _record_failed_manifest(
            manifest,
            manifest_path,
            ValidationIssue(
                "manifest-scope",
                "Rollback manifest must describe matching moves, patches, and objects",
            ),
        )
    allowed_unmanaged = {move.to_relative_path for move in manifest.moves}
    if not set(manifest.unmanaged_files_to_delete).issubset(allowed_unmanaged):
        return _record_failed_manifest(
            manifest,
            manifest_path,
            ValidationIssue(
                "manifest-scope",
                "Unmanaged cleanup paths must match the recorded move target",
            ),
        )

    root = manifest.project_root.resolve()
    try:
        resolved_moves = [
            (
                move,
                resolve_project_path(root, move.from_relative_path),
                resolve_project_path(root, move.to_relative_path),
            )
            for move in manifest.moves
        ]
        resolved_work_units = {
            relative_path: resolve_project_path(root, relative_path)
            for relative_path in dict.fromkeys(
                patched.relative_path for patched in manifest.patched_files
            )
        }
        created_directories = [
            resolve_project_path(root, relative_path)
            for relative_path in manifest.created_directories
        ]
    except UnsafeProjectPath as exc:
        return _record_failed_manifest(
            manifest,
            manifest_path,
            ValidationIssue("outside-project", str(exc)),
        )

    issues: list[ValidationIssue] = []
    for directory in created_directories:
        if directory == root or (directory.exists() and not directory.is_dir()):
            issues.append(
                ValidationIssue(
                    "rollback-directory-drift",
                    f"Recorded created directory is not safe to remove: {directory}",
                )
            )
    pending_moves: list[tuple[Path, Path]] = []
    duplicate_targets: list[Path] = []
    for move, source, target in resolved_moves:
        if not move.source_sha256:
            issues.append(
                ValidationIssue(
                    "rollback-source-hash-missing",
                    f"Local rollback has no recorded WAV hash: {target}",
                )
            )
            continue
        source_exists = source.exists()
        target_exists = target.exists()
        if not source_exists and not target_exists:
            issues.append(
                ValidationIssue(
                    "rollback-wav-state-drift",
                    "A recorded WAV is missing from both rollback paths: "
                    f"{source} / {target}",
                )
            )
            continue
        duplicate_target = False
        if source_exists and target_exists:
            try:
                duplicate_target = source.samefile(target)
            except OSError:
                duplicate_target = False
            if not duplicate_target:
                issues.append(
                    ValidationIssue(
                        "rollback-wav-state-drift",
                        "Both rollback paths contain different files: "
                        f"{source} / {target}",
                    )
                )
                continue
        current = source if source_exists else target
        if not current.is_file():
            issues.append(
                ValidationIssue(
                    "rollback-wav-state-drift",
                    f"Recorded WAV path is not a file: {current}",
                )
            )
            continue
        try:
            digest = _sha256_file(current)
        except OSError as exc:
            issues.append(
                ValidationIssue(
                    "rollback-wav-read-failed",
                    f"Recorded WAV could not be read: {current}: {exc}",
                )
            )
            continue
        if digest != move.source_sha256:
            issues.append(
                ValidationIssue(
                    "rollback-wav-drift",
                    f"Recorded WAV changed after local apply: {current}",
                )
            )
            continue
        if duplicate_target:
            duplicate_targets.append(target)
        elif target_exists:
            pending_moves.append((source, target))

    reverse_patches: dict[str, PreparedWwuPatch] = {}
    for relative_path, work_unit in resolved_work_units.items():
        records = tuple(
            patched
            for patched in manifest.patched_files
            if patched.relative_path == relative_path
        )
        original_hashes = {record.original_sha256 for record in records}
        patched_hashes = {record.patched_sha256 for record in records}
        if len(original_hashes) != 1 or len(patched_hashes) != 1:
            issues.append(
                ValidationIssue(
                    "rollback-wwu-records-invalid",
                    f"Work Unit hashes are inconsistent: {work_unit}",
                )
            )
            continue
        if not work_unit.is_file():
            issues.append(
                ValidationIssue(
                    "rollback-wwu-missing", f"Work Unit is missing: {work_unit}"
                )
            )
            continue
        try:
            digest = _sha256_file(work_unit)
        except OSError as exc:
            issues.append(
                ValidationIssue(
                    "rollback-wwu-read-failed",
                    f"Work Unit could not be read: {work_unit}: {exc}",
                )
            )
            continue
        original_hash = next(iter(original_hashes))
        patched_hash = next(iter(patched_hashes))
        if digest == original_hash:
            continue
        if digest != patched_hash:
            issues.append(
                ValidationIssue(
                    "rollback-wwu-drift",
                    f"Work Unit changed after local apply: {work_unit}",
                )
            )
            continue
        try:
            reverse = prepare_source_path_patches(
                work_unit,
                changes=tuple(
                    (
                        record.object_guid,
                        record.new_xml_path,
                        record.old_xml_path,
                    )
                    for record in records
                ),
            )
        except WwuPatchError as exc:
            issues.append(ValidationIssue("rollback-wwu-patch-invalid", str(exc)))
            continue
        if (
            reverse.original_sha256 != patched_hash
            or reverse.patched_sha256 != original_hash
        ):
            issues.append(
                ValidationIssue(
                    "rollback-wwu-patch-mismatch",
                    f"Reverse Work Unit patch does not reproduce the original: {work_unit}",
                )
            )
            continue
        reverse_patches[relative_path] = reverse

    if issues:
        return _record_failed_manifest_issues(manifest, manifest_path, issues)

    for relative_path, reverse in reverse_patches.items():
        work_unit = resolved_work_units[relative_path]
        try:
            write_prepared_patch(work_unit, reverse)
        except (OSError, WwuPatchError) as exc:
            issues.append(
                ValidationIssue(
                    "rollback-wwu-write-failed",
                    f"Work Unit could not be restored: {work_unit}: {exc}",
                )
            )
            break

    if not issues:
        for source, target in reversed(pending_moves):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                move_file_no_replace(target, source)
            except OSError as exc:
                issues.append(
                    ValidationIssue(
                        "rollback-local-move-failed",
                        f"WAV could not be restored: {target} -> {source}: {exc}",
                    )
                )
                break

    if not issues:
        for target in reversed(duplicate_targets):
            try:
                target.unlink()
            except OSError as exc:
                issues.append(
                    ValidationIssue(
                        "rollback-local-move-failed",
                        f"Duplicate target could not be removed: {target}: {exc}",
                    )
                )
                break

    if not issues:
        for directory in sorted(
            created_directories,
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            if not directory.exists():
                continue
            try:
                if any(directory.iterdir()):
                    continue
                directory.rmdir()
            except OSError as exc:
                try:
                    became_nonempty = directory.exists() and any(
                        directory.iterdir()
                    )
                except OSError:
                    became_nonempty = False
                if became_nonempty:
                    continue
                issues.append(
                    ValidationIssue(
                        "rollback-directory-remove-failed",
                        f"Created directory could not be removed: {directory}: {exc}",
                    )
                )
                break

    if not issues:
        for move, source, target in resolved_moves:
            if not source.is_file() or target.exists():
                issues.append(
                    ValidationIssue(
                        "rollback-wav-state-drift",
                        f"WAV was not restored to its source path: {source}",
                    )
                )
                continue
            if move.source_sha256 and _sha256_file(source) != move.source_sha256:
                issues.append(
                    ValidationIssue(
                        "rollback-wav-drift",
                        f"Restored WAV does not match its recorded hash: {source}",
                    )
                )
        for relative_path, work_unit in resolved_work_units.items():
            original_hashes = {
                record.original_sha256
                for record in manifest.patched_files
                if record.relative_path == relative_path
            }
            if (
                not work_unit.is_file()
                or len(original_hashes) != 1
                or _sha256_file(work_unit) not in original_hashes
            ):
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


def _record_failed_manifest_issues(
    manifest: RollbackManifest,
    manifest_path: str | Path | None,
    issues: list[ValidationIssue],
) -> ValidationResult:
    if manifest_path is not None:
        write_json_document(manifest.with_status("failed"), manifest_path)
    return ValidationResult(tuple(issues))


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
from typing import Protocol

from .models import RollbackManifest, ValidationIssue, ValidationResult
from .p4_client import P4Client
from .project_paths import UnsafeProjectPath, resolve_project_path
from .wwise_xml import WwuParseError, source_path_count_for_guid


class WaapiConnection(Protocol):
    def call(
        self,
        uri: str,
        args: dict[str, object],
        *,
        options: dict[str, object],
    ) -> dict[str, object]: ...


def validate_applied_manifest(
    manifest: RollbackManifest, *, p4: P4Client
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    root = manifest.project_root.resolve()

    if (
        len(manifest.moves) != 1
        or len(manifest.patched_files) != 1
        or len(manifest.affected_objects) != 1
    ):
        return ValidationResult(
            (
                ValidationIssue(
                    "manifest-scope",
                    "Apply manifest must describe exactly one move, patch, and object",
                ),
            )
        )

    resolved_moves: list[tuple[Path, Path]] = []
    resolved_work_units: list[Path] = []
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
    except UnsafeProjectPath as exc:
        return ValidationResult((ValidationIssue("outside-project", str(exc)),))

    for source, target in resolved_moves:
        if source.exists():
            issues.append(
                ValidationIssue("source-still-exists", f"Source WAV still exists: {source}")
            )
        if not target.is_file():
            issues.append(
                ValidationIssue("target-missing", f"Target WAV is missing: {target}")
            )

    for patched, work_unit in zip(manifest.patched_files, resolved_work_units):
        if not work_unit.is_file():
            issues.append(
                ValidationIssue(
                    "work-unit-missing", f"Work Unit is missing: {work_unit}"
                )
            )
            continue
        digest = hashlib.sha256(work_unit.read_bytes()).hexdigest()
        if digest != patched.patched_sha256:
            issues.append(
                ValidationIssue(
                    "unexpected-wwu-diff",
                    f"Work Unit contains changes beyond the prepared patch: {work_unit}",
                )
            )
        try:
            old_count = source_path_count_for_guid(
                work_unit,
                object_guid=patched.object_guid,
                relative_path=patched.old_xml_path,
            )
            new_count = source_path_count_for_guid(
                work_unit,
                object_guid=patched.object_guid,
                relative_path=patched.new_xml_path,
            )
        except WwuParseError as exc:
            issues.append(ValidationIssue("work-unit-invalid", str(exc)))
            continue
        if old_count:
            issues.append(
                ValidationIssue(
                    "old-source-present",
                    f"Old source path remains in the target Sound: {patched.old_xml_path}",
                )
            )
        if new_count != 1:
            issues.append(
                ValidationIssue(
                    "new-source-mismatch",
                    f"New source path is not unique in the target Sound: {patched.new_xml_path}",
                )
            )

    absolute_paths = [path for move in resolved_moves for path in move]
    absolute_paths.extend(resolved_work_units)
    try:
        opened = p4.run(p4.opened(*absolute_paths)).stdout.casefold()
    except (OSError, subprocess.CalledProcessError) as exc:
        issues.append(ValidationIssue("p4-opened-failed", str(exc)))
    else:
        if "move/add" not in opened or "move/delete" not in opened:
            issues.append(
                ValidationIssue(
                    "p4-move-missing",
                    "p4 opened does not show both move/add and move/delete",
                )
            )
        if " edit " not in opened:
            issues.append(
                ValidationIssue(
                    "p4-edit-missing", "p4 opened does not show the Work Unit as edit"
                )
            )

    for patched, work_unit in zip(manifest.patched_files, resolved_work_units):
        try:
            diff = p4.run(p4.diff(work_unit)).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            issues.append(ValidationIssue("p4-diff-failed", str(exc)))
            continue
        if not _diff_contains_only_path_change(
            diff, patched.old_xml_path, patched.new_xml_path
        ):
            issues.append(
                ValidationIssue(
                    "unsafe-p4-diff",
                    f"Work Unit diff is not limited to the source path: {work_unit}",
                )
            )

    return ValidationResult(tuple(issues))


def validate_live_wwise_manifest(
    manifest: RollbackManifest, *, connection: WaapiConnection
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    for affected in manifest.affected_objects:
        response = connection.call(
            "ak.wwise.core.object.get",
            {"from": {"path": [affected.object_path]}},
            options={
                "return": [
                    "id",
                    "path",
                    "originalRelativeFilePath",
                    "originalFilePath",
                ]
            },
        )
        if not isinstance(response, dict):
            issues.append(
                ValidationIssue(
                    "wwise-response-invalid",
                    "WAAPI did not return a response object",
                    affected.object_path,
                )
            )
            continue
        records = response.get("return")
        if not isinstance(records, list) or len(records) != 1:
            issues.append(
                ValidationIssue(
                    "wwise-object-missing",
                    "WAAPI did not return exactly one affected Wwise object",
                    affected.object_path,
                )
            )
            continue
        record = records[0]
        if not isinstance(record, dict):
            issues.append(
                ValidationIssue(
                    "wwise-object-invalid",
                    "WAAPI returned an invalid object record",
                    affected.object_path,
                )
            )
            continue
        if record.get("id") != affected.guid:
            issues.append(
                ValidationIssue(
                    "wwise-guid-changed",
                    "Affected Wwise object GUID does not match the manifest",
                    affected.object_path,
                )
            )
        if record.get("path") != affected.object_path:
            issues.append(
                ValidationIssue(
                    "wwise-path-changed",
                    "Affected Wwise object path does not match the manifest",
                    affected.object_path,
                )
            )
        relative = record.get("originalRelativeFilePath")
        source_matches = isinstance(relative, str) and _canonical_source_path(
            relative
        ) == _canonical_source_path(affected.after_source_relative_path)
        if not source_matches:
            issues.append(
                ValidationIssue(
                    "wwise-source-mismatch",
                    "Wwise has not loaded the relocated source path",
                    affected.object_path,
                )
            )
        original_file = record.get("originalFilePath")
        if not isinstance(original_file, str) or not _waapi_file_exists(
            original_file
        ):
            issues.append(
                ValidationIssue(
                    "wwise-source-missing",
                    "WAAPI does not report an existing original source file",
                    affected.object_path,
                )
            )
    return ValidationResult(tuple(issues))


def validate_live_wwise_manifest_at_url(
    manifest: RollbackManifest, *, url: str | None = None
) -> ValidationResult:
    try:
        from waapi import WaapiClient
    except ImportError as exc:
        raise RuntimeError(
            "Live Wwise validation requires the optional waapi-client dependency"
        ) from exc
    kwargs = {} if url is None else {"url": url}
    try:
        with WaapiClient(**kwargs) as connection:
            return validate_live_wwise_manifest(manifest, connection=connection)
    except Exception as exc:
        raise RuntimeError(f"Live Wwise validation failed: {exc}") from exc


def _diff_contains_only_path_change(diff: str, old_path: str, new_path: str) -> bool:
    removed = [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    added = [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    if len(removed) != 1 or len(added) != 1:
        return False
    return old_path in removed[0] and removed[0].replace(old_path, new_path) == added[0]


def _canonical_source_path(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("/")
    if not normalized.casefold().startswith("originals/"):
        normalized = f"Originals/{normalized}"
    return normalized.casefold()


def _waapi_file_exists(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if normalized.casefold().startswith("z:/") and os.name != "nt":
        normalized = normalized[2:]
    return Path(normalized).is_file()

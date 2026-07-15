from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
from typing import Protocol
from urllib.parse import urlparse

from .models import (
    AffectedObjectRecord,
    RollbackManifest,
    ValidationIssue,
    ValidationResult,
)
from .p4_client import P4Client
from .project_paths import UnsafeProjectPath, resolve_project_path
from .waapi_transport import HttpWaapiConnection, WaapiCallError
from .wwise_xml import WwuParseError, source_path_count_for_guid


DEFAULT_LIVE_WWISE_BATCH_SIZE = 32


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
        not manifest.moves
        or len(manifest.moves) != len(manifest.patched_files)
        or len(manifest.moves) != len(manifest.affected_objects)
    ):
        return ValidationResult(
            (
                ValidationIssue(
                    "manifest-scope",
                    "Apply manifest must describe matching moves, patches, and objects",
                ),
            )
        )

    resolved_moves: list[tuple[Path, Path]] = []
    resolved_work_units: dict[str, Path] = {}
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

    checked_hashes: set[str] = set()
    for patched in manifest.patched_files:
        work_unit = resolved_work_units[patched.relative_path]
        if not work_unit.is_file():
            issues.append(
                ValidationIssue(
                    "work-unit-missing", f"Work Unit is missing: {work_unit}"
                )
            )
            continue
        if patched.relative_path not in checked_hashes:
            checked_hashes.add(patched.relative_path)
            expected_hashes = {
                record.patched_sha256
                for record in manifest.patched_files
                if record.relative_path == patched.relative_path
            }
            digest = hashlib.sha256(work_unit.read_bytes()).hexdigest()
            if len(expected_hashes) != 1 or digest not in expected_hashes:
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
    absolute_paths.extend(resolved_work_units.values())
    try:
        opened = p4.run(p4.opened(*absolute_paths)).stdout.casefold()
    except (OSError, subprocess.CalledProcessError) as exc:
        issues.append(ValidationIssue("p4-opened-failed", str(exc)))
    else:
        if (
            opened.count("move/add") < len(manifest.moves)
            or opened.count("move/delete") < len(manifest.moves)
        ):
            issues.append(
                ValidationIssue(
                    "p4-move-missing",
                    "p4 opened does not show both move/add and move/delete",
                )
            )
        if opened.count(" edit ") < len(resolved_work_units):
            issues.append(
                ValidationIssue(
                    "p4-edit-missing", "p4 opened does not show the Work Unit as edit"
                )
            )

    for relative_path, work_unit in resolved_work_units.items():
        try:
            diff = p4.run(p4.diff(work_unit)).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            issues.append(ValidationIssue("p4-diff-failed", str(exc)))
            continue
        changes = tuple(
            (patched.old_xml_path, patched.new_xml_path)
            for patched in manifest.patched_files
            if patched.relative_path == relative_path
        )
        if not _diff_contains_only_path_changes(diff, changes):
            issues.append(
                ValidationIssue(
                    "unsafe-p4-diff",
                    f"Work Unit diff is not limited to the source path: {work_unit}",
                )
            )

    return ValidationResult(tuple(issues))


def validate_live_wwise_manifest(
    manifest: RollbackManifest,
    *,
    connection: WaapiConnection,
    batch_size: int = DEFAULT_LIVE_WWISE_BATCH_SIZE,
) -> ValidationResult:
    if batch_size <= 0:
        raise ValueError("Live Wwise validation batch size must be positive")

    issues: list[ValidationIssue] = []
    affected_objects = manifest.affected_objects
    for offset in range(0, len(affected_objects), batch_size):
        batch = affected_objects[offset : offset + batch_size]
        response = connection.call(
            "ak.wwise.core.object.get",
            {"from": {"path": [affected.object_path for affected in batch]}},
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
            issues.extend(
                ValidationIssue(
                    "wwise-response-invalid",
                    "WAAPI did not return a response object",
                    affected.object_path,
                )
                for affected in batch
            )
            continue
        records = response.get("return")
        if not isinstance(records, list):
            issues.extend(
                ValidationIssue(
                    "wwise-response-invalid",
                    "WAAPI did not return an object list",
                    affected.object_path,
                )
                for affected in batch
            )
            continue
        valid_records = [record for record in records if isinstance(record, dict)]
        if len(valid_records) != len(records):
            issues.append(
                ValidationIssue(
                    "wwise-object-invalid",
                    "WAAPI returned an invalid object record",
                    batch[0].object_path,
                )
            )

        used_record_indices: set[int] = set()
        for affected in batch:
            matching_records = [
                (index, record)
                for index, record in enumerate(valid_records)
                if index not in used_record_indices
                and isinstance(record.get("id"), str)
                and record["id"].casefold() == affected.guid.casefold()
            ]
            if not matching_records:
                matching_records = [
                    (index, record)
                    for index, record in enumerate(valid_records)
                    if index not in used_record_indices
                    and record.get("path") == affected.object_path
                ]
            if not matching_records and len(batch) == 1 and len(valid_records) == 1:
                # A single stale result can still provide detailed identity diagnostics.
                matching_records = [(0, valid_records[0])]
            if not matching_records:
                issues.append(
                    ValidationIssue(
                        "wwise-object-missing",
                        "WAAPI did not return the affected Wwise object",
                        affected.object_path,
                    )
                )
                continue
            if len(matching_records) > 1:
                issues.append(
                    ValidationIssue(
                        "wwise-object-ambiguous",
                        "WAAPI returned multiple records for the affected Wwise object",
                        affected.object_path,
                    )
                )
                continue
            record_index, record = matching_records[0]
            used_record_indices.add(record_index)
            issues.extend(_validate_live_wwise_record(affected, record))
    return ValidationResult(tuple(issues))


def _validate_live_wwise_record(
    affected: AffectedObjectRecord, record: dict[str, object]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    record_id = record.get("id")
    if (
        not isinstance(record_id, str)
        or record_id.casefold() != affected.guid.casefold()
    ):
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
    if not isinstance(original_file, str) or not _waapi_file_exists(original_file):
        issues.append(
            ValidationIssue(
                "wwise-source-missing",
                "WAAPI does not report an existing original source file",
                affected.object_path,
            )
        )
    return issues


def validate_live_wwise_manifest_at_url(
    manifest: RollbackManifest, *, url: str | None = None
) -> ValidationResult:
    scheme = urlparse(url).scheme if url is not None else None
    if url is not None and scheme not in {"ws", "wss", "http", "https"}:
        raise RuntimeError(
            "Live Wwise validation requires a ws://, wss://, http://, or https:// URL"
        )
    if scheme in {"http", "https"}:
        try:
            return validate_live_wwise_manifest(
                manifest,
                connection=HttpWaapiConnection(url, timeout=20.0),
            )
        except (WaapiCallError, OSError, ValueError) as exc:
            raise RuntimeError(f"Live Wwise validation failed: {exc}") from exc

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
    return _diff_contains_only_path_changes(diff, ((old_path, new_path),))


def _diff_contains_only_path_changes(
    diff: str, changes: tuple[tuple[str, str], ...]
) -> bool:
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
    if len(removed) != len(changes) or len(added) != len(changes):
        return False
    unmatched_added = list(added)
    for old_path, new_path in changes:
        matching_removed = [line for line in removed if old_path in line]
        if len(matching_removed) != 1:
            return False
        expected_added = matching_removed[0].replace(old_path, new_path)
        if expected_added not in unmatched_added:
            return False
        unmatched_added.remove(expected_added)
    return not unmatched_added


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

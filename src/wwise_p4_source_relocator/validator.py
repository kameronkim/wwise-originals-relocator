from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
from typing import Protocol
from urllib.parse import urlparse

from .models import (
    AffectedObjectRecord,
    RollbackManifest,
    ValidationIssue,
    ValidationResult,
)
from .p4_client import P4Client, parse_p4_tagged_records
from .project_paths import UnsafeProjectPath, resolve_project_path
from .waapi_transport import HttpWaapiConnection, WaapiCallError
from .wwise_xml import WwuParseError, source_path_count_for_guid


DEFAULT_LIVE_WWISE_BATCH_SIZE = 32
DEFAULT_P4_VALIDATION_BATCH_SIZE = 32


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

    perforce_issue_start = len(issues)
    perforce_issues, perforce_summary = _validate_perforce_changelist(
        manifest,
        resolved_moves=resolved_moves,
        resolved_work_units=tuple(resolved_work_units.values()),
        p4=p4,
    )
    issues.extend(perforce_issues)

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

    perforce_summary["valid"] = len(issues) == perforce_issue_start
    return ValidationResult(
        tuple(issues),
        details={"perforce": perforce_summary},
    )


def _validate_perforce_changelist(
    manifest: RollbackManifest,
    *,
    resolved_moves: tuple[tuple[Path, Path], ...],
    resolved_work_units: tuple[Path, ...],
    p4: P4Client,
) -> tuple[tuple[ValidationIssue, ...], dict[str, object]]:
    expected_change = manifest.changelist or "default"
    expected_actions: dict[str, tuple[Path, str]] = {}
    for source, target in resolved_moves:
        expected_actions[_local_path_key(source)] = (source, "move/delete")
        expected_actions[_local_path_key(target)] = (target, "move/add")
    for work_unit in resolved_work_units:
        expected_actions[_local_path_key(work_unit)] = (work_unit, "edit")

    issues: list[ValidationIssue] = []
    records: list[dict[str, str]] = []
    expected_paths = [value[0] for value in expected_actions.values()]
    try:
        for offset in range(0, len(expected_paths), DEFAULT_P4_VALIDATION_BATCH_SIZE):
            batch = expected_paths[offset : offset + DEFAULT_P4_VALIDATION_BATCH_SIZE]
            result = p4.run(p4.fstat_opened(*batch))
            records.extend(parse_p4_tagged_records(result.stdout))
    except (OSError, subprocess.CalledProcessError) as exc:
        issues.append(ValidationIssue("p4-opened-failed", str(exc)))

    records_by_local_path: dict[str, list[dict[str, str]]] = {}
    for record in records:
        local_path = record.get("clientFile") or record.get("path")
        if local_path:
            records_by_local_path.setdefault(_local_path_key(local_path), []).append(
                record
            )

    matched_records: dict[str, dict[str, str]] = {}
    action_counts = {"move/add": 0, "move/delete": 0, "edit": 0}
    for path_key, (path, expected_action) in expected_actions.items():
        path_records = records_by_local_path.get(path_key, [])
        if len(path_records) != 1:
            qualifier = "not reported" if not path_records else "reported more than once"
            issues.append(
                ValidationIssue(
                    "p4-action-mismatch",
                    f"Expected {expected_action}, but the path was {qualifier}",
                    str(path),
                )
            )
            continue
        record = path_records[0]
        matched_records[path_key] = record
        actual_action = record.get("action", "")
        if actual_action != expected_action:
            issues.append(
                ValidationIssue(
                    "p4-action-mismatch",
                    f"Expected {expected_action}, but Perforce reports {actual_action or 'no action'}",
                    str(path),
                )
            )
        else:
            action_counts[expected_action] += 1
        actual_change = record.get("change", "default") or "default"
        if actual_change != expected_change:
            issues.append(
                ValidationIssue(
                    "p4-changelist-mismatch",
                    f"Expected changelist {expected_change}, but Perforce reports {actual_change}",
                    str(path),
                )
            )

    paired_moves = 0
    for source, target in resolved_moves:
        source_record = matched_records.get(_local_path_key(source))
        target_record = matched_records.get(_local_path_key(target))
        if source_record is None or target_record is None:
            continue
        source_depot = source_record.get("depotFile", "")
        target_depot = target_record.get("depotFile", "")
        source_pair = source_record.get("movedFile", "")
        target_pair = target_record.get("movedFile", "")
        if (
            source_depot
            and target_depot
            and _depot_path_key(source_pair) == _depot_path_key(target_depot)
            and _depot_path_key(target_pair) == _depot_path_key(source_depot)
        ):
            paired_moves += 1
        else:
            issues.append(
                ValidationIssue(
                    "p4-move-pair-mismatch",
                    "Perforce does not link the move/delete and move/add records as one move",
                    f"{source} -> {target}",
                )
            )

    expected_depot_files = {
        _depot_path_key(record["depotFile"]): record["depotFile"]
        for record in matched_records.values()
        if record.get("depotFile")
    }
    actual_depot_files: dict[str, str] = {}
    if not any(issue.code == "p4-opened-failed" for issue in issues):
        try:
            opened = p4.run(p4.opened(changelist=expected_change)).stdout
            actual_depot_files = {
                _depot_path_key(depot_file): depot_file
                for depot_file in _opened_depot_files(opened)
            }
        except (OSError, subprocess.CalledProcessError) as exc:
            issues.append(ValidationIssue("p4-opened-failed", str(exc)))

    unexpected_keys = actual_depot_files.keys() - expected_depot_files.keys()
    missing_keys = expected_depot_files.keys() - actual_depot_files.keys()
    unexpected_files = sorted(actual_depot_files[key] for key in unexpected_keys)
    missing_files = sorted(expected_depot_files[key] for key in missing_keys)
    if unexpected_files:
        issues.append(
            ValidationIssue(
                "p4-changelist-extra-files",
                "The changelist contains files outside this relocation operation: "
                + ", ".join(unexpected_files),
            )
        )
    if missing_files:
        issues.append(
            ValidationIssue(
                "p4-changelist-missing-files",
                "The changelist is missing files from this relocation operation: "
                + ", ".join(missing_files),
            )
        )

    summary: dict[str, object] = {
        "changelist": expected_change,
        "isDefault": manifest.changelist is None,
        "expectedMoveCount": len(resolved_moves),
        "moveAddCount": action_counts["move/add"],
        "moveDeleteCount": action_counts["move/delete"],
        "movePairCount": paired_moves,
        "expectedWorkUnitCount": len(resolved_work_units),
        "workUnitEditCount": action_counts["edit"],
        "expectedFileCount": len(expected_actions),
        "actualFileCount": len(actual_depot_files),
        "unexpectedFileCount": len(unexpected_files),
        "missingFileCount": len(missing_files),
        "unexpectedFiles": unexpected_files,
        "missingFiles": missing_files,
        "valid": False,
    }
    return tuple(issues), summary


def _opened_depot_files(output: str) -> tuple[str, ...]:
    depot_files: list[str] = []
    for line in output.splitlines():
        match = re.match(r"^(//.+?)#\d+\s+-\s+", line.strip())
        if match:
            depot_files.append(match.group(1))
    return tuple(depot_files)


def _local_path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path))).casefold()


def _depot_path_key(path: str) -> str:
    return path.replace("\\", "/").casefold()


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

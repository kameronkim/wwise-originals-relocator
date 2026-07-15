from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Sequence
from pathlib import Path

from .models import (
    AffectedObjectRecord,
    MoveRecord,
    PatchedFileRecord,
    RelocationPlan,
    RelocationPlanItem,
    RollbackManifest,
    ValidationIssue,
    ValidationResult,
)
from .p4_client import P4Client
from .preflight import WorkspaceProbe, validate_relocation_plan
from .project_paths import resolve_project_path
from .report import write_json_document
from .rollback import rollback_manifest
from .validator import validate_applied_manifest
from .wwise_xml import prepare_source_path_patches, write_prepared_patch


class ApplyError(RuntimeError):
    pass


def apply_single_file(
    plan: RelocationPlan,
    *,
    only: str,
    manifest_path: str | Path,
    p4: P4Client,
    probe: WorkspaceProbe | None = None,
) -> tuple[RollbackManifest, ValidationResult]:
    return apply_selected_files(
        plan,
        only=(only,),
        manifest_path=manifest_path,
        p4=p4,
        probe=probe,
    )


def apply_selected_files(
    plan: RelocationPlan,
    *,
    only: Sequence[str],
    manifest_path: str | Path,
    p4: P4Client,
    probe: WorkspaceProbe | None = None,
) -> tuple[RollbackManifest, ValidationResult]:
    items = _select_items(plan, only)
    selected_plan = RelocationPlan(
        project_root=plan.project_root,
        object_root=plan.object_root,
        chapter=plan.chapter,
        items=items,
    )
    preflight = validate_relocation_plan(selected_plan, probe=probe)
    if not preflight.is_valid:
        details = "; ".join(issue.message for issue in preflight.issues)
        raise ApplyError(f"Preflight failed: {details}")

    root = plan.project_root.resolve()
    item_paths: list[tuple[RelocationPlanItem, Path, Path]] = []
    items_by_work_unit: dict[str, list[RelocationPlanItem]] = {}
    for item in items:
        if not item.from_relative_path or not item.to_relative_path:
            raise ApplyError("Selected plan item does not contain a complete move")
        item_paths.append(
            (
                item,
                resolve_project_path(root, item.from_relative_path),
                resolve_project_path(root, item.to_relative_path),
            )
        )
        items_by_work_unit.setdefault(item.work_unit_path, []).append(item)

    patches = {}
    for work_unit_path, work_unit_items in items_by_work_unit.items():
        work_unit = resolve_project_path(root, work_unit_path)
        patches[work_unit_path] = prepare_source_path_patches(
            work_unit,
            changes=tuple(
                (
                    item.guid,
                    item.from_relative_path or "",
                    item.to_relative_path or "",
                )
                for item in work_unit_items
            ),
        )

    patched_files: list[PatchedFileRecord] = []
    for work_unit_path, work_unit_items in items_by_work_unit.items():
        patch = patches[work_unit_path]
        for item, change in zip(work_unit_items, patch.changes):
            patched_files.append(
                PatchedFileRecord(
                    relative_path=work_unit_path,
                    object_guid=item.guid,
                    old_xml_path=change.old_xml_path,
                    new_xml_path=change.new_xml_path,
                    original_sha256=patch.original_sha256,
                    patched_sha256=patch.patched_sha256,
                )
            )
    manifest = RollbackManifest(
        created_at=datetime.now(timezone.utc).isoformat(),
        project_root=root,
        changelist=None,
        moves=tuple(
            MoveRecord(item.from_relative_path or "", item.to_relative_path or "")
            for item in items
        ),
        patched_files=tuple(patched_files),
        affected_objects=tuple(
            AffectedObjectRecord(
                object_path=item.object_path,
                guid=item.guid,
                before_source_relative_path=item.from_relative_path or "",
                after_source_relative_path=item.to_relative_path or "",
            )
            for item in items
        ),
        unmanaged_files_to_delete=(),
    )
    write_json_document(manifest, manifest_path)

    opened_work_units: set[str] = set()
    moved_items: list[RelocationPlanItem] = []
    try:
        for work_unit_path in items_by_work_unit:
            work_unit = resolve_project_path(root, work_unit_path)
            p4.run(p4.edit(work_unit))
            opened_work_units.add(work_unit_path)
        for item, source, target in item_paths:
            p4.run(p4.edit(source))
            try:
                p4.run(p4.move(source, target))
            except Exception:
                p4.run(p4.revert(source))
                raise
            moved_items.append(item)
        for work_unit_path, patch in patches.items():
            write_prepared_patch(resolve_project_path(root, work_unit_path), patch)
        validation = validate_applied_manifest(manifest, p4=p4)
        if not validation.is_valid:
            details = "; ".join(issue.message for issue in validation.issues)
            raise ApplyError(f"Post-apply validation failed: {details}")
    except Exception as exc:
        recovery_guids = {item.guid.casefold() for item in moved_items}
        recovery_work_units = {
            item.work_unit_path for item in moved_items
        }
        cleanup_issues: list[ValidationIssue] = []
        for work_unit_path in opened_work_units - recovery_work_units:
            try:
                p4.run(
                    p4.revert(
                        resolve_project_path(root, work_unit_path),
                    )
                )
            except Exception as cleanup_exc:
                cleanup_issues.append(
                    ValidationIssue("p4-revert-wwu-failed", str(cleanup_exc))
                )
        recovery = RollbackManifest(
            created_at=manifest.created_at,
            project_root=manifest.project_root,
            changelist=manifest.changelist,
            moves=tuple(
                MoveRecord(item.from_relative_path or "", item.to_relative_path or "")
                for item in moved_items
            ),
            patched_files=tuple(
                record
                for record in manifest.patched_files
                if record.object_guid.casefold() in recovery_guids
            ),
            affected_objects=tuple(
                record
                for record in manifest.affected_objects
                if record.guid.casefold() in recovery_guids
            ),
            unmanaged_files_to_delete=(),
        )
        write_json_document(recovery, manifest_path)
        if recovery.moves:
            rollback_result = rollback_manifest(
                recovery, p4=p4, manifest_path=manifest_path
            )
            rollback = ValidationResult(
                rollback_result.issues + tuple(cleanup_issues)
            )
            if cleanup_issues:
                write_json_document(recovery.with_status("failed"), manifest_path)
        else:
            rollback = ValidationResult(tuple(cleanup_issues))
            write_json_document(
                (manifest if rollback.is_valid else recovery).with_status(
                    "rolled-back" if rollback.is_valid else "failed"
                ),
                manifest_path,
            )
        if isinstance(exc, ApplyError):
            message = str(exc)
        else:
            message = f"Apply failed: {exc}"
        if not rollback.is_valid:
            rollback_details = "; ".join(
                issue.message for issue in rollback.issues
            )
            message = f"{message}; automatic rollback failed: {rollback_details}"
        raise ApplyError(message) from exc

    awaiting_reload = manifest.with_status("awaiting-wwise-reload")
    write_json_document(awaiting_reload, manifest_path)
    return awaiting_reload, validation


def _select_items(
    plan: RelocationPlan, source_file_names: Sequence[str]
) -> tuple[RelocationPlanItem, ...]:
    requested = tuple(name.strip() for name in source_file_names if name.strip())
    if not requested:
        raise ApplyError("At least one move candidate must be selected")
    if len({name.casefold() for name in requested}) != len(requested):
        raise ApplyError("A move candidate was selected more than once")

    selected: list[RelocationPlanItem] = []
    for source_file_name in requested:
        matches = [
            item
            for item in plan.items
            if item.action == "move-and-patch"
            and item.source_file_name == source_file_name
        ]
        if len(matches) != 1:
            raise ApplyError(
                "Each selected file must match exactly one move candidate; "
                f"{source_file_name!r} found {len(matches)}"
            )
        selected.append(matches[0])
    normalized_guids = {item.guid.casefold() for item in selected}
    source_paths = {
        (item.from_relative_path or "").replace("\\", "/").casefold()
        for item in selected
    }
    target_paths = {
        (item.to_relative_path or "").replace("\\", "/").casefold()
        for item in selected
    }
    if len(normalized_guids) != len(selected):
        raise ApplyError("Selected items contain the same Wwise object more than once")
    if len(source_paths) != len(selected) or len(target_paths) != len(selected):
        raise ApplyError("Selected items contain a duplicate source or target path")
    if source_paths & target_paths:
        raise ApplyError("Selected source and target paths overlap")
    return tuple(selected)

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import (
    AffectedObjectRecord,
    MoveRecord,
    PatchedFileRecord,
    RelocationPlan,
    RelocationPlanItem,
    RollbackManifest,
    ValidationResult,
)
from .p4_client import P4Client
from .preflight import WorkspaceProbe, validate_relocation_plan
from .project_paths import resolve_project_path
from .report import write_json_document
from .rollback import rollback_manifest
from .validator import validate_applied_manifest
from .wwise_xml import prepare_source_path_patch, write_prepared_patch


class ApplyError(RuntimeError):
    pass


def apply_single_file(
    plan: RelocationPlan,
    *,
    only: str,
    changelist: str | None,
    manifest_path: str | Path,
    p4: P4Client,
    probe: WorkspaceProbe | None = None,
) -> tuple[RollbackManifest, ValidationResult]:
    item = _select_single_item(plan, only)
    selected_plan = RelocationPlan(
        project_root=plan.project_root,
        object_root=plan.object_root,
        chapter=plan.chapter,
        items=(item,),
    )
    preflight = validate_relocation_plan(selected_plan, probe=probe)
    if not preflight.is_valid:
        details = "; ".join(issue.message for issue in preflight.issues)
        raise ApplyError(f"Preflight failed: {details}")

    if not item.from_relative_path or not item.to_relative_path:
        raise ApplyError("Selected plan item does not contain a complete move")
    root = plan.project_root.resolve()
    source = resolve_project_path(root, item.from_relative_path)
    target = resolve_project_path(root, item.to_relative_path)
    work_unit = resolve_project_path(root, item.work_unit_path)
    patch = prepare_source_path_patch(
        work_unit,
        object_guid=item.guid,
        old_relative_path=item.from_relative_path,
        new_relative_path=item.to_relative_path,
    )
    manifest = RollbackManifest(
        created_at=datetime.now(timezone.utc).isoformat(),
        project_root=root,
        changelist=changelist,
        moves=(MoveRecord(item.from_relative_path, item.to_relative_path),),
        patched_files=(
            PatchedFileRecord(
                relative_path=item.work_unit_path,
                object_guid=item.guid,
                old_xml_path=patch.old_xml_path,
                new_xml_path=patch.new_xml_path,
                original_sha256=patch.original_sha256,
                patched_sha256=patch.patched_sha256,
            ),
        ),
        affected_objects=(
            AffectedObjectRecord(
                object_path=item.object_path,
                guid=item.guid,
                before_source_relative_path=item.from_relative_path,
                after_source_relative_path=item.to_relative_path,
            ),
        ),
        unmanaged_files_to_delete=(),
    )
    write_json_document(manifest, manifest_path)

    try:
        p4.run(p4.edit(work_unit, changelist=changelist))
        p4.run(p4.edit(source, changelist=changelist))
        p4.run(p4.move(source, target, changelist=changelist))
        write_prepared_patch(work_unit, patch)
        validation = validate_applied_manifest(manifest, p4=p4)
        if not validation.is_valid:
            details = "; ".join(issue.message for issue in validation.issues)
            raise ApplyError(f"Post-apply validation failed: {details}")
    except Exception as exc:
        rollback = rollback_manifest(
            manifest, p4=p4, manifest_path=manifest_path
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

    applied = manifest.with_status("applied")
    write_json_document(applied, manifest_path)
    return applied, validation


def _select_single_item(
    plan: RelocationPlan, source_file_name: str
) -> RelocationPlanItem:
    matches = [
        item
        for item in plan.items
        if item.action == "move-and-patch"
        and item.source_file_name == source_file_name
    ]
    if len(matches) != 1:
        raise ApplyError(
            f"--only must select exactly one move candidate; found {len(matches)}"
        )
    return matches[0]

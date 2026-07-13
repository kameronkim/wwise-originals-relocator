from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Iterable

from .models import (
    NoOpPlan,
    NoOpPlanItem,
    RelocationPlan,
    RelocationPlanItem,
    ScanResult,
    SourceItem,
    SourceReference,
)


SUPPORTED_CATEGORIES = frozenset({"Cutscene", "Script", "Dialog", "Dynamic"})


def build_noop_plan(
    project_root: str | Path, references: Iterable[SourceReference]
) -> NoOpPlan:
    """Create an inspection-only plan that cannot request file mutations."""

    return NoOpPlan(
        project_root=Path(project_root).resolve(),
        items=tuple(NoOpPlanItem(source=reference) for reference in references),
    )


def build_relocation_plan(scan: ScanResult) -> RelocationPlan:
    source_counts = Counter(
        path.casefold()
        for item in scan.items
        for path in item.source_relative_paths
        if len(item.source_relative_paths) == 1
    )
    items = tuple(_plan_item(item, source_counts) for item in scan.items)
    return RelocationPlan(
        project_root=scan.project_root,
        object_root=scan.object_root,
        chapter=scan.chapter,
        items=items,
    )


def _plan_item(item: SourceItem, source_counts: Counter[str]) -> RelocationPlanItem:
    common = {
        "object_path": item.object_path,
        "guid": item.guid,
        "source_file_name": item.source_file_name,
        "from_relative_path": item.current_source_relative_path,
        "work_unit_path": item.work_unit_path,
    }
    if len(item.source_relative_paths) == 0:
        return RelocationPlanItem(
            **common,
            to_relative_path=None,
            action="manual-review",
            reason="Wwise object has no audio source",
        )
    if len(item.source_relative_paths) > 1:
        return RelocationPlanItem(
            **common,
            to_relative_path=None,
            action="manual-review",
            reason="Wwise object has multiple audio sources",
        )
    source_path = item.source_relative_paths[0]
    if source_counts[source_path.casefold()] > 1:
        return RelocationPlanItem(
            **common,
            to_relative_path=None,
            action="manual-review",
            reason="Multiple Wwise objects share the same source WAV",
        )
    if not source_path.casefold().startswith("originals/"):
        return RelocationPlanItem(
            **common,
            to_relative_path=None,
            action="manual-review",
            reason="Source path is not under Originals",
        )
    if item.category not in SUPPORTED_CATEGORIES:
        return RelocationPlanItem(
            **common,
            to_relative_path=None,
            action="manual-review",
            reason="Object tree category is not supported",
        )
    if not item.language:
        return RelocationPlanItem(
            **common,
            to_relative_path=None,
            action="manual-review",
            reason="Language cannot be inferred",
        )
    if not item.chapter:
        return RelocationPlanItem(
            **common,
            to_relative_path=None,
            action="manual-review",
            reason="Chapter cannot be inferred",
        )

    target = PurePosixPath(
        "Originals",
        "Voices",
        item.language,
        item.category,
        item.chapter,
        item.source_file_name or "",
    ).as_posix()
    if source_path.casefold() == target.casefold():
        return RelocationPlanItem(
            **common,
            to_relative_path=target,
            action="skip",
            reason="Source is already in the category folder",
        )
    return RelocationPlanItem(
        **common,
        to_relative_path=target,
        action="move-and-patch",
    )

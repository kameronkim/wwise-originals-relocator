from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .models import NoOpPlan, NoOpPlanItem, SourceReference


def build_noop_plan(
    project_root: str | Path, references: Iterable[SourceReference]
) -> NoOpPlan:
    """Create an inspection-only plan that cannot request file mutations."""

    return NoOpPlan(
        project_root=Path(project_root).resolve(),
        items=tuple(NoOpPlanItem(source=reference) for reference in references),
    )

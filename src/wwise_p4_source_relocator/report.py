from __future__ import annotations

import json
from pathlib import Path

from .models import NoOpPlan


def write_json_plan(plan: NoOpPlan, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def render_markdown_plan(plan: NoOpPlan) -> str:
    lines = [
        "# Wwise Source Inspection",
        "",
        "## Summary",
        "",
        f"- Sources discovered: {len(plan.items)}",
        "- Move candidates: 0",
        f"- Skipped by inspection-only policy: {len(plan.items)}",
        "- Mutations performed: 0",
        "",
        "## Discovered Sources",
        "",
        "| Sound | Source | Audio file | Work Unit | Action |",
        "|---|---|---|---|---|",
    ]
    for item in plan.items:
        source = item.source
        lines.append(
            "| "
            + " | ".join(
                _escape(value)
                for value in (
                    source.object_name,
                    source.source_name,
                    source.source_relative_path,
                    source.work_unit_path,
                    item.action,
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def write_markdown_plan(plan: NoOpPlan, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_plan(plan), encoding="utf-8")


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")

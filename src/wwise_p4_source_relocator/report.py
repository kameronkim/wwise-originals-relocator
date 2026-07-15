from __future__ import annotations

import json
from pathlib import Path

from .models import (
    NoOpPlan,
    RelocationPlan,
    RollbackManifest,
    ScanResult,
    ValidationResult,
)


def write_json_plan(plan: NoOpPlan, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_json_document(document: object, output_path: str | Path) -> None:
    if not hasattr(document, "to_dict"):
        raise TypeError("JSON document must provide to_dict()")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_scan_result(input_path: str | Path) -> ScanResult:
    return ScanResult.from_dict(_read_json_object(input_path))


def read_relocation_plan(input_path: str | Path) -> RelocationPlan:
    return RelocationPlan.from_dict(_read_json_object(input_path))


def read_rollback_manifest(input_path: str | Path) -> RollbackManifest:
    return RollbackManifest.from_dict(_read_json_object(input_path))


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


def render_relocation_plan(plan: RelocationPlan) -> str:
    counts = {
        action: sum(item.action == action for item in plan.items)
        for action in ("move-and-patch", "skip", "manual-review")
    }
    lines = [
        f"# Relocation Plan - {plan.chapter}",
        "",
        "## Summary",
        "",
        f"- Total objects scanned: {len(plan.items)}",
        f"- Move candidates: {counts['move-and-patch']}",
        f"- Already correct: {counts['skip']}",
        f"- Manual review: {counts['manual-review']}",
        "",
        "## Items",
        "",
        "| Object | From | To | Work Unit | Action | Reason |",
        "|---|---|---|---|---|---|",
    ]
    for item in plan.items:
        lines.append(
            "| "
            + " | ".join(
                _escape(value or "")
                for value in (
                    item.object_path,
                    item.from_relative_path,
                    item.to_relative_path,
                    item.work_unit_path,
                    item.action,
                    item.reason,
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def render_validation(result: ValidationResult) -> str:
    lines = [
        "# Relocation Validation",
        "",
        f"- Valid: {'yes' if result.is_valid else 'no'}",
        f"- Issues: {len(result.issues)}",
    ]
    perforce = (result.details or {}).get("perforce")
    if isinstance(perforce, dict):
        lines.extend(
            (
                "",
                "## Perforce Changelist",
                "",
                f"- Changelist: `{_detail(perforce, 'changelist')}`",
                "- WAV move/add: "
                f"{_detail(perforce, 'moveAddCount')} / "
                f"{_detail(perforce, 'expectedMoveCount')}",
                "- WAV move/delete: "
                f"{_detail(perforce, 'moveDeleteCount')} / "
                f"{_detail(perforce, 'expectedMoveCount')}",
                "- Linked move pairs: "
                f"{_detail(perforce, 'movePairCount')} / "
                f"{_detail(perforce, 'expectedMoveCount')}",
                "- Work Unit edits: "
                f"{_detail(perforce, 'workUnitEditCount')} / "
                f"{_detail(perforce, 'expectedWorkUnitCount')}",
                "- Changelist files: "
                f"{_detail(perforce, 'actualFileCount')} / "
                f"{_detail(perforce, 'expectedFileCount')}",
                "- Unexpected files: "
                f"{_detail(perforce, 'unexpectedFileCount')}",
                f"- Missing files: {_detail(perforce, 'missingFileCount')}",
            )
        )
    if result.issues:
        lines.extend(("", "## Issues", ""))
        lines.extend(
            f"- `{issue.code}`: {issue.message}"
            + (f" ({issue.object_path})" if issue.object_path else "")
            for issue in result.issues
        )
    return "\n".join(lines) + "\n"


def _detail(values: dict[str, object], key: str) -> object:
    return values.get(key, "—")


def _read_json_object(input_path: str | Path) -> dict[str, object]:
    value = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {input_path}")
    return value


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")

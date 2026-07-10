from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class SourceReference:
    """An audio-file reference discovered beneath a Wwise Sound object."""

    object_name: str
    object_guid: str
    source_name: str
    source_guid: str
    source_relative_path: str
    language: str | None
    work_unit_path: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "objectName": self.object_name,
            "objectGuid": self.object_guid,
            "sourceName": self.source_name,
            "sourceGuid": self.source_guid,
            "sourceRelativePath": self.source_relative_path,
            "language": self.language,
            "workUnitPath": self.work_unit_path,
        }


@dataclass(frozen=True, slots=True)
class NoOpPlanItem:
    """Inspection plan entry that explicitly authorizes no mutation."""

    source: SourceReference
    action: Literal["skip"] = "skip"
    reason: str = "Source inspection only; relocation rules are not applied"

    def to_dict(self) -> dict[str, object]:
        return {
            **self.source.to_dict(),
            "action": self.action,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class NoOpPlan:
    project_root: Path
    items: tuple[NoOpPlanItem, ...]
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "projectRoot": self.project_root.as_posix(),
            "mode": "no-op",
            "items": [item.to_dict() for item in self.items],
        }

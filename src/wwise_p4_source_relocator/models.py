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


@dataclass(frozen=True, slots=True)
class SourceItem:
    object_path: str
    guid: str
    category: str | None
    source_relative_paths: tuple[str, ...]
    work_unit_path: str
    language: str | None
    chapter: str | None

    @property
    def source_file_name(self) -> str | None:
        if len(self.source_relative_paths) != 1:
            return None
        return Path(self.source_relative_paths[0]).name

    @property
    def current_source_relative_path(self) -> str | None:
        if len(self.source_relative_paths) != 1:
            return None
        return self.source_relative_paths[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "objectPath": self.object_path,
            "guid": self.guid,
            "category": self.category,
            "currentSourceRelativePath": self.current_source_relative_path,
            "sourceRelativePaths": list(self.source_relative_paths),
            "sourceFileName": self.source_file_name,
            "workUnitPath": self.work_unit_path,
            "language": self.language,
            "chapter": self.chapter,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "SourceItem":
        paths = value.get("sourceRelativePaths")
        if paths is None:
            current = value.get("currentSourceRelativePath")
            paths = [] if current is None else [current]
        if not isinstance(paths, list) or not all(
            isinstance(path, str) for path in paths
        ):
            raise ValueError("sourceRelativePaths must be a list of strings")
        return cls(
            object_path=_required_string(value, "objectPath"),
            guid=_required_string(value, "guid"),
            category=_optional_string(value.get("category")),
            source_relative_paths=tuple(paths),
            work_unit_path=_required_string(value, "workUnitPath"),
            language=_optional_string(value.get("language")),
            chapter=_optional_string(value.get("chapter")),
        )


@dataclass(frozen=True, slots=True)
class ScanResult:
    project_root: Path
    object_root: str
    chapter: str
    items: tuple[SourceItem, ...]
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "projectRoot": self.project_root.as_posix(),
            "objectRoot": self.object_root,
            "chapter": self.chapter,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ScanResult":
        raw_items = value.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("Scan result items must be a list")
        if not all(isinstance(item, dict) for item in raw_items):
            raise ValueError("Every scan result item must be an object")
        return cls(
            project_root=Path(_required_string(value, "projectRoot")),
            object_root=_required_string(value, "objectRoot"),
            chapter=_required_string(value, "chapter"),
            items=tuple(SourceItem.from_dict(item) for item in raw_items),
            schema_version=int(value.get("schemaVersion", 1)),
        )


PlanAction = Literal["move-and-patch", "skip", "manual-review"]


@dataclass(frozen=True, slots=True)
class RelocationPlanItem:
    object_path: str
    guid: str
    source_file_name: str | None
    from_relative_path: str | None
    to_relative_path: str | None
    work_unit_path: str
    action: PlanAction
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "objectPath": self.object_path,
            "guid": self.guid,
            "sourceFileName": self.source_file_name,
            "from": self.from_relative_path,
            "to": self.to_relative_path,
            "workUnitPath": self.work_unit_path,
            "action": self.action,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RelocationPlanItem":
        action = _required_string(value, "action")
        if action not in {"move-and-patch", "skip", "manual-review"}:
            raise ValueError(f"Unsupported plan action: {action}")
        return cls(
            object_path=_required_string(value, "objectPath"),
            guid=_required_string(value, "guid"),
            source_file_name=_optional_string(value.get("sourceFileName")),
            from_relative_path=_optional_string(value.get("from")),
            to_relative_path=_optional_string(value.get("to")),
            work_unit_path=_required_string(value, "workUnitPath"),
            action=action,
            reason=_optional_string(value.get("reason")),
        )


@dataclass(frozen=True, slots=True)
class RelocationPlan:
    project_root: Path
    object_root: str
    chapter: str
    items: tuple[RelocationPlanItem, ...]
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "projectRoot": self.project_root.as_posix(),
            "objectRoot": self.object_root,
            "chapter": self.chapter,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RelocationPlan":
        raw_items = value.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("Relocation plan items must be a list")
        if not all(isinstance(item, dict) for item in raw_items):
            raise ValueError("Every relocation plan item must be an object")
        return cls(
            project_root=Path(_required_string(value, "projectRoot")),
            object_root=_required_string(value, "objectRoot"),
            chapter=_required_string(value, "chapter"),
            items=tuple(RelocationPlanItem.from_dict(item) for item in raw_items),
            schema_version=int(value.get("schemaVersion", 1)),
        )


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    object_path: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "objectPath": self.object_path,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.is_valid,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _required_string(value: dict[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{key} must be a non-empty string")
    return raw


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected a string or null")
    return value or None

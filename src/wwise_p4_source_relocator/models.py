from __future__ import annotations

from dataclasses import dataclass, replace
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


@dataclass(frozen=True, slots=True)
class MoveRecord:
    from_relative_path: str
    to_relative_path: str

    def to_dict(self) -> dict[str, str]:
        return {"from": self.from_relative_path, "to": self.to_relative_path}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "MoveRecord":
        return cls(
            from_relative_path=_required_string(value, "from"),
            to_relative_path=_required_string(value, "to"),
        )


@dataclass(frozen=True, slots=True)
class PatchedFileRecord:
    relative_path: str
    object_guid: str
    old_xml_path: str
    new_xml_path: str
    original_sha256: str
    patched_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.relative_path,
            "objectGuid": self.object_guid,
            "oldXmlPath": self.old_xml_path,
            "newXmlPath": self.new_xml_path,
            "originalSha256": self.original_sha256,
            "patchedSha256": self.patched_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "PatchedFileRecord":
        return cls(
            relative_path=_required_string(value, "path"),
            object_guid=_required_string(value, "objectGuid"),
            old_xml_path=_required_string(value, "oldXmlPath"),
            new_xml_path=_required_string(value, "newXmlPath"),
            original_sha256=_required_string(value, "originalSha256"),
            patched_sha256=_required_string(value, "patchedSha256"),
        )


@dataclass(frozen=True, slots=True)
class AffectedObjectRecord:
    object_path: str
    guid: str
    before_source_relative_path: str
    after_source_relative_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "objectPath": self.object_path,
            "guid": self.guid,
            "beforeSourceRelativePath": self.before_source_relative_path,
            "afterSourceRelativePath": self.after_source_relative_path,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "AffectedObjectRecord":
        return cls(
            object_path=_required_string(value, "objectPath"),
            guid=_required_string(value, "guid"),
            before_source_relative_path=_required_string(
                value, "beforeSourceRelativePath"
            ),
            after_source_relative_path=_required_string(
                value, "afterSourceRelativePath"
            ),
        )


ManifestStatus = Literal["prepared", "applied", "rolled-back", "failed"]


@dataclass(frozen=True, slots=True)
class RollbackManifest:
    created_at: str
    project_root: Path
    changelist: str | None
    moves: tuple[MoveRecord, ...]
    patched_files: tuple[PatchedFileRecord, ...]
    affected_objects: tuple[AffectedObjectRecord, ...]
    unmanaged_files_to_delete: tuple[str, ...]
    status: ManifestStatus = "prepared"
    schema_version: int = 1

    def with_status(self, status: ManifestStatus) -> "RollbackManifest":
        return replace(self, status=status)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "createdAt": self.created_at,
            "projectRoot": self.project_root.as_posix(),
            "changelist": self.changelist,
            "status": self.status,
            "moves": [move.to_dict() for move in self.moves],
            "patchedFiles": [record.to_dict() for record in self.patched_files],
            "affectedObjects": [
                record.to_dict() for record in self.affected_objects
            ],
            "unmanagedFilesToDelete": list(self.unmanaged_files_to_delete),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RollbackManifest":
        moves = _object_list(value, "moves")
        patched_files = _object_list(value, "patchedFiles")
        affected_objects = _object_list(value, "affectedObjects")
        unmanaged = value.get("unmanagedFilesToDelete", [])
        if not isinstance(unmanaged, list) or not all(
            isinstance(path, str) for path in unmanaged
        ):
            raise ValueError("unmanagedFilesToDelete must be a list of strings")
        status = _required_string(value, "status")
        if status not in {"prepared", "applied", "rolled-back", "failed"}:
            raise ValueError(f"Unsupported manifest status: {status}")
        return cls(
            created_at=_required_string(value, "createdAt"),
            project_root=Path(_required_string(value, "projectRoot")),
            changelist=_optional_string(value.get("changelist")),
            moves=tuple(MoveRecord.from_dict(record) for record in moves),
            patched_files=tuple(
                PatchedFileRecord.from_dict(record) for record in patched_files
            ),
            affected_objects=tuple(
                AffectedObjectRecord.from_dict(record)
                for record in affected_objects
            ),
            unmanaged_files_to_delete=tuple(unmanaged),
            status=status,
            schema_version=int(value.get("schemaVersion", 1)),
        )


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


def _object_list(value: dict[str, object], key: str) -> list[dict[str, object]]:
    raw = value.get(key)
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError(f"{key} must be a list of objects")
    return raw

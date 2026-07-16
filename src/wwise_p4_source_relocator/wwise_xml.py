from __future__ import annotations

import codecs
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any
from xml.sax.saxutils import escape  # nosec B406

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from .models import SourceReference


class WwuParseError(ValueError):
    """Raised when a work unit is malformed or lacks required identity data."""


class WwuPatchError(ValueError):
    """Raised when an exact, identity-scoped WWU patch cannot be proven safe."""


@dataclass(frozen=True, slots=True)
class PreparedSourcePathChange:
    object_guid: str
    old_xml_path: str
    new_xml_path: str


@dataclass(frozen=True, slots=True)
class PreparedWwuPatch:
    original_bytes: bytes
    patched_bytes: bytes
    changes: tuple[PreparedSourcePathChange, ...]
    encoding: str

    @property
    def old_xml_path(self) -> str:
        return self.changes[0].old_xml_path

    @property
    def new_xml_path(self) -> str:
        return self.changes[0].new_xml_path

    @property
    def original_sha256(self) -> str:
        return hashlib.sha256(self.original_bytes).hexdigest()

    @property
    def patched_sha256(self) -> str:
        return hashlib.sha256(self.patched_bytes).hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children_named(element: Any, name: str):
    for child in element.iter():
        if child is not element and _local_name(child.tag) == name:
            yield child


def _first_text(element: Any, name: str) -> str | None:
    for child in _children_named(element, name):
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def parse_source_references(
    wwu_path: str | Path, *, project_root: str | Path | None = None
) -> tuple[SourceReference, ...]:
    """Read exact AudioFile values while preserving their owning object IDs.

    The parser deliberately does not infer categories, chapters, or relocation
    destinations. Those decisions belong to the scanner and planner.
    """

    path = Path(wwu_path)
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError, DefusedXmlException) as exc:
        raise WwuParseError(
            f"Unable to parse Wwise work unit {path}: {exc}"
        ) from exc

    root_path = Path(project_root) if project_root is not None else None
    if root_path is not None:
        try:
            display_path = path.resolve().relative_to(root_path.resolve()).as_posix()
        except ValueError as exc:
            raise WwuParseError(
                f"Work unit {path} is outside project root {root_path}"
            ) from exc
    else:
        display_path = path.as_posix()

    references: list[SourceReference] = []
    for sound in (node for node in root.iter() if _local_name(node.tag) == "Sound"):
        object_name = sound.get("Name", "").strip()
        object_guid = sound.get("ID", "").strip()
        if not object_name or not object_guid:
            raise WwuParseError("A Sound element is missing its Name or ID attribute")

        for source in _children_named(sound, "AudioFileSource"):
            source_name = source.get("Name", "").strip()
            source_guid = source.get("ID", "").strip()
            if not source_name or not source_guid:
                raise WwuParseError(
                    f"AudioFileSource under {object_name!r} is missing Name or ID"
                )

            audio_files = [
                node.text.strip()
                for node in _children_named(source, "AudioFile")
                if node.text and node.text.strip()
            ]
            for audio_file in audio_files:
                references.append(
                    SourceReference(
                        object_name=object_name,
                        object_guid=object_guid,
                        source_name=source_name,
                        source_guid=source_guid,
                        source_relative_path=audio_file.replace("\\", "/"),
                        language=_first_text(source, "Language"),
                        work_unit_path=display_path,
                    )
                )

    return tuple(references)


def prepare_source_path_patch(
    wwu_path: str | Path,
    *,
    object_guid: str,
    old_relative_path: str,
    new_relative_path: str,
) -> PreparedWwuPatch:
    """Prepare one exact text replacement without serializing the XML tree."""

    return prepare_source_path_patches(
        wwu_path,
        changes=((object_guid, old_relative_path, new_relative_path),),
    )


def prepare_source_path_patches(
    wwu_path: str | Path,
    *,
    changes: tuple[tuple[str, str, str], ...],
) -> PreparedWwuPatch:
    """Prepare identity-scoped replacements as one atomic Work Unit patch."""

    if not changes:
        raise WwuPatchError("At least one source path change is required")

    path = Path(wwu_path)
    try:
        original = path.read_bytes()
    except OSError as exc:
        raise WwuPatchError(f"Unable to read Wwise work unit {path}: {exc}") from exc

    encoding = _detect_xml_encoding(original)
    try:
        text = original.decode(encoding)
        root = ElementTree.fromstring(original)
    except (UnicodeError, ElementTree.ParseError, DefusedXmlException) as exc:
        raise WwuPatchError(
            f"Unable to decode Wwise work unit {path}: {exc}"
        ) from exc

    prepared_changes: list[PreparedSourcePathChange] = []
    patched_text = text
    seen_guids: set[str] = set()
    for object_guid, old_relative_path, new_relative_path in changes:
        normalized_guid = object_guid.casefold()
        if normalized_guid in seen_guids:
            raise WwuPatchError(
                f"Sound GUID {object_guid} was selected more than once"
            )
        seen_guids.add(normalized_guid)
        sounds = [
            node
            for node in root.iter()
            if _local_name(node.tag) == "Sound"
            and node.get("ID", "").casefold() == normalized_guid
        ]
        if len(sounds) != 1:
            raise WwuPatchError(
                f"Expected one Sound with GUID {object_guid}, found {len(sounds)}"
            )

        matches = [
            node
            for node in _children_named(sounds[0], "AudioFile")
            if node.text
            and _source_paths_equivalent(node.text, old_relative_path)
        ]
        if len(matches) != 1:
            raise WwuPatchError(
                "Expected one exact AudioFile path under the target Sound, "
                f"found {len(matches)}"
            )

        old_xml_path = matches[0].text.strip()
        new_xml_path = _match_wwu_path_style(old_xml_path, new_relative_path)
        escaped_old = escape(old_xml_path)
        escaped_new = escape(new_xml_path)
        if patched_text.count(escaped_old) != 1:
            raise WwuPatchError(
                "The exact source path appears more than once in the Work Unit"
            )
        patched_text = patched_text.replace(escaped_old, escaped_new, 1)
        prepared_changes.append(
            PreparedSourcePathChange(object_guid, old_xml_path, new_xml_path)
        )
    try:
        patched = patched_text.encode(encoding)
        ElementTree.fromstring(patched)
    except (UnicodeError, ElementTree.ParseError, DefusedXmlException) as exc:
        raise WwuPatchError(
            f"Patched Work Unit would be invalid XML: {exc}"
        ) from exc

    return PreparedWwuPatch(
        original_bytes=original,
        patched_bytes=patched,
        changes=tuple(prepared_changes),
        encoding=encoding,
    )


def write_prepared_patch(wwu_path: str | Path, patch: PreparedWwuPatch) -> None:
    path = Path(wwu_path)
    if path.read_bytes() != patch.original_bytes:
        raise WwuPatchError("Work Unit changed after the patch was prepared")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(patch.patched_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes() != patch.original_bytes:
            raise WwuPatchError("Work Unit changed after the patch was prepared")
        _replace_file(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _replace_file(source: Path, target: Path) -> None:
    os.replace(source, target)


def source_path_count_for_guid(
    wwu_path: str | Path, *, object_guid: str, relative_path: str
) -> int:
    try:
        root = ElementTree.parse(wwu_path).getroot()
    except (OSError, ElementTree.ParseError, DefusedXmlException) as exc:
        raise WwuParseError(
            f"Unable to parse Wwise work unit {wwu_path}: {exc}"
        ) from exc
    sounds = [
        node
        for node in root.iter()
        if _local_name(node.tag) == "Sound"
        and node.get("ID", "").casefold() == object_guid.casefold()
    ]
    if len(sounds) != 1:
        return 0
    expected = _canonical_source_path(relative_path)
    return sum(
        node.text is not None and _source_paths_equivalent(node.text, expected)
        for node in _children_named(sounds[0], "AudioFile")
    )


def _canonical_source_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/").lstrip("/")
    if not normalized.casefold().startswith("originals/"):
        normalized = f"Originals/{normalized}"
    return normalized.casefold()


def _source_paths_equivalent(left: str, right: str) -> bool:
    return not _source_path_forms(left).isdisjoint(_source_path_forms(right))


def _source_path_forms(value: str) -> set[str]:
    normalized = value.strip().replace("\\", "/").lstrip("/")
    forms = {normalized.casefold()}
    parts = PurePosixPath(normalized).parts
    if parts and parts[0].casefold() == "originals":
        parts = parts[1:]
        forms.add(PurePosixPath(*parts).as_posix().casefold())
    if parts and parts[0].casefold() == "sfx" and len(parts) > 1:
        forms.add(PurePosixPath(*parts[1:]).as_posix().casefold())
    if parts and parts[0].casefold() == "voices" and len(parts) > 2:
        forms.add(PurePosixPath(*parts[2:]).as_posix().casefold())
    return forms


def _match_wwu_path_style(old_xml_path: str, new_relative_path: str) -> str:
    normalized = new_relative_path.replace("\\", "/").lstrip("/")
    old_normalized = old_xml_path.replace("\\", "/").lstrip("/")
    if not old_normalized.casefold().startswith("originals/"):
        if normalized.casefold().startswith("originals/"):
            normalized = normalized[len("Originals/") :]
        old_root = old_normalized.split("/", 1)[0].casefold()
        parts = PurePosixPath(normalized).parts
        if old_root not in {"voices", "sfx"}:
            if parts and parts[0].casefold() == "sfx":
                normalized = PurePosixPath(*parts[1:]).as_posix()
            elif len(parts) > 2 and parts[0].casefold() == "voices":
                normalized = PurePosixPath(*parts[2:]).as_posix()
    separator = "\\" if "\\" in old_xml_path else "/"
    return normalized.replace("/", separator)


def _detect_xml_encoding(data: bytes) -> str:
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le"
    if data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be"
    declaration = data[:256].decode("ascii", errors="ignore")
    match = re.search(r"encoding=[\"']([^\"']+)[\"']", declaration, re.IGNORECASE)
    return match.group(1) if match else "utf-8"

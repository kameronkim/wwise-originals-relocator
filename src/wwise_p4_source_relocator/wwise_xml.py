from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from .models import SourceReference


class WwuParseError(ValueError):
    """Raised when a work unit is malformed or lacks required identity data."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children_named(element: ElementTree.Element, name: str):
    for child in element.iter():
        if child is not element and _local_name(child.tag) == name:
            yield child


def _first_text(element: ElementTree.Element, name: str) -> str | None:
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
    except (OSError, ElementTree.ParseError) as exc:
        raise WwuParseError(f"Unable to parse Wwise work unit {path}: {exc}") from exc

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

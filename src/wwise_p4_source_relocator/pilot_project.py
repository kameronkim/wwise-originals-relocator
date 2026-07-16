from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tempfile
from typing import Iterable
import wave

from .wwise_xml import WwuParseError, parse_source_references


class PilotProjectError(RuntimeError):
    """Raised when a disposable Wwise pilot project cannot be created safely."""


@dataclass(frozen=True, slots=True)
class PilotProjectItem:
    category: str
    object_path: str
    sound_name: str
    source_relative_path: str
    target_relative_path: str
    work_unit_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "objectPath": self.object_path,
            "soundName": self.sound_name,
            "sourceRelativePath": self.source_relative_path,
            "targetRelativePath": self.target_relative_path,
            "workUnitPath": self.work_unit_path,
        }


@dataclass(frozen=True, slots=True)
class PilotProject:
    project_root: Path
    project_file: Path
    object_root: str
    chapter: str
    items: tuple[PilotProjectItem, ...]

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schemaVersion": 2,
            "projectRoot": self.project_root.as_posix(),
            "projectFile": self.project_file.as_posix(),
            "objectRoot": self.object_root,
            "chapter": self.chapter,
            "itemCount": len(self.items),
            "items": [item.to_dict() for item in self.items],
        }
        if self.items:
            # Keep the original single-item metadata fields readable by older tools.
            value.update(
                {
                    "soundName": self.sound_name,
                    "sourceRelativePath": self.source_relative_path,
                    "targetRelativePath": self.target_relative_path,
                    "workUnitPath": self.work_unit_path,
                }
            )
        return value

    @property
    def sound_name(self) -> str:
        return self._first_item.sound_name

    @property
    def source_relative_path(self) -> str:
        return self._first_item.source_relative_path

    @property
    def target_relative_path(self) -> str:
        return self._first_item.target_relative_path

    @property
    def work_unit_path(self) -> str:
        return self._first_item.work_unit_path

    @property
    def _first_item(self) -> PilotProjectItem:
        if not self.items:
            raise PilotProjectError("Pilot project contains no fixture items")
        return self.items[0]


@dataclass(frozen=True, slots=True)
class PilotImportRow:
    audio_file: Path
    object_path: str
    originals_subfolder: str

    def to_tsv_row(self) -> tuple[str, str, str, str]:
        return (
            str(self.audio_file.resolve()),
            self.object_path,
            "Sound Voice",
            self.originals_subfolder,
        )


@dataclass(frozen=True, slots=True)
class _PilotItemDefinition:
    category: str
    sound_name: str
    frequency_hz: float


def _pilot_item_definitions(
    chapter: str,
    *,
    category: str | None,
    sound_name: str | None,
) -> tuple[_PilotItemDefinition, ...]:
    if category is None and sound_name is None:
        return (
            _PilotItemDefinition("Script", f"{chapter}_S102_WT_001", 440.0),
            _PilotItemDefinition("Dialog", f"{chapter}_S103_DI_001", 554.37),
            _PilotItemDefinition("Cutscene", f"{chapter}_S104_SQ_001", 659.25),
        )
    resolved_category = category or "Script"
    resolved_sound_name = sound_name or f"{chapter}_S102_WT_001"
    if not resolved_category.strip() or not resolved_sound_name.strip():
        raise PilotProjectError("Category and sound name must not be empty")
    return (
        _PilotItemDefinition(
            resolved_category,
            resolved_sound_name,
            440.0,
        ),
    )


def create_pilot_project(
    project_root: str | Path,
    *,
    wwise_console: str | Path,
    project_name: str = "WwiseRelocatorPilot",
    platform: str = "Mac",
    language: str = "English(US)",
    object_root: str = r"\Containers\Default Work Unit\VO\Temp_VO",
    category: str | None = None,
    chapter: str = "CH04",
    source_category: str = "Scenario",
    sound_name: str | None = None,
) -> PilotProject:
    root = Path(project_root).expanduser().resolve()
    console = Path(wwise_console).expanduser().resolve()
    _validate_new_project_root(root)
    if not project_name or Path(project_name).name != project_name:
        raise PilotProjectError("Project name must be a single non-empty path component")
    if root.name != project_name:
        raise PilotProjectError(
            "Wwise requires the project folder and .wproj name to match; "
            f"use a project root ending in /{project_name}"
        )
    if not console.is_file():
        raise PilotProjectError(f"WwiseConsole was not found: {console}")

    definitions = _pilot_item_definitions(
        chapter,
        category=category,
        sound_name=sound_name,
    )
    items = tuple(
        PilotProjectItem(
            category=definition.category,
            object_path=_join_wwise_path(
                object_root,
                definition.category,
                chapter,
                definition.sound_name,
            ),
            sound_name=definition.sound_name,
            source_relative_path=PurePosixPath(
                "Originals",
                "Voices",
                language,
                source_category,
                chapter,
                f"{definition.sound_name}.wav",
            ).as_posix(),
            target_relative_path=PurePosixPath(
                "Originals",
                "Voices",
                language,
                definition.category,
                chapter,
                f"{definition.sound_name}.wav",
            ).as_posix(),
            work_unit_path="",
        )
        for definition in definitions
    )
    project_file = root / f"{project_name}.wproj"

    _run_console(console, "create-new-project", project_file, "--platform", platform)
    with tempfile.TemporaryDirectory(prefix="wwise-relocator-import-") as temporary:
        import_dir = Path(temporary)
        import_file = import_dir / "pilot-import.tsv"
        import_rows = []
        for definition, item in zip(definitions, items, strict=True):
            source_wav = import_dir / f"{definition.sound_name}.wav"
            write_test_tone(source_wav, frequency_hz=definition.frequency_hz)
            import_rows.append(
                PilotImportRow(
                    audio_file=source_wav,
                    object_path=item.object_path,
                    originals_subfolder=f"{source_category}\\{chapter}",
                )
            )
        write_import_tsv_rows(
            import_file,
            import_rows,
        )
        _run_console(
            console,
            "tab-delimited-import",
            project_file,
            import_file,
            "--import-language",
            language,
            "--tab-delimited-operation",
            "createNew",
            "--no-source-control",
        )
    pilot = _inspect_created_project(
        root,
        project_file=project_file,
        object_root=object_root,
        chapter=chapter,
        items=items,
    )
    metadata = root / "relocator-pilot.json"
    metadata.write_text(
        json.dumps(pilot.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return pilot


def write_test_tone(
    path: str | Path,
    *,
    sample_rate: int = 48_000,
    duration_seconds: float = 0.25,
    frequency_hz: float = 440.0,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame_count = round(sample_rate * duration_seconds)
    amplitude = 0.2 * ((1 << 15) - 1)
    frames = b"".join(
        struct.pack(
            "<h",
            round(amplitude * math.sin(2.0 * math.pi * frequency_hz * index / sample_rate)),
        )
        for index in range(frame_count)
    )
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)


def write_import_tsv(
    path: str | Path,
    *,
    audio_file: str | Path,
    object_path: str,
    originals_subfolder: str,
) -> None:
    write_import_tsv_rows(
        path,
        (
            PilotImportRow(
                audio_file=Path(audio_file),
                object_path=object_path,
                originals_subfolder=originals_subfolder,
            ),
        ),
    )


def write_import_tsv_rows(
    path: str | Path,
    rows: Iterable[PilotImportRow],
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    import_rows = tuple(rows)
    if not import_rows:
        raise PilotProjectError("At least one Wwise import row is required")
    tsv_rows = (
        ("Audio File", "Object Path", "Object Type", "OriginalsSubFolder"),
        *(row.to_tsv_row() for row in import_rows),
    )
    destination.write_text(
        "\n".join("\t".join(row) for row in tsv_rows) + "\n", encoding="utf-8"
    )


def find_wwise_console() -> Path | None:
    candidates = sorted(
        Path("/Applications/Audiokinetic").glob(
            "Wwise_*/Wwise.app/Contents/Tools/WwiseConsole.sh"
        ),
        reverse=True,
    )
    resolved = next((path.resolve() for path in candidates if path.is_file()), None)
    if resolved is not None:
        return resolved
    executable = shutil.which("WwiseConsole") or shutil.which("WwiseConsole.sh")
    return Path(executable).resolve() if executable else None


def _validate_new_project_root(root: Path) -> None:
    if root.exists():
        try:
            has_entries = any(root.iterdir())
        except OSError as exc:
            raise PilotProjectError(f"Unable to inspect project root {root}: {exc}") from exc
        if has_entries:
            raise PilotProjectError(
                f"Pilot project root must be new or empty; refusing to modify {root}"
            )


def _run_console(console: Path, operation: str, *arguments: object) -> None:
    command = (str(console), operation, *(str(argument) for argument in arguments))
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise PilotProjectError(f"Unable to start WwiseConsole: {exc}") from exc
    if result.returncode != 0:
        details = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        raise PilotProjectError(
            f"WwiseConsole {operation} failed with exit code {result.returncode}"
            + (f":\n{details}" if details else "")
        )


def _inspect_created_project(
    root: Path,
    *,
    project_file: Path,
    object_root: str,
    chapter: str,
    items: tuple[PilotProjectItem, ...],
) -> PilotProject:
    if not project_file.is_file():
        raise PilotProjectError(f"Wwise project was not created: {project_file}")
    if not items:
        raise PilotProjectError("Generated Wwise project has no fixture items")
    for item in items:
        source_file = root / Path(item.source_relative_path)
        if not source_file.is_file():
            raise PilotProjectError(
                f"Imported Originals WAV was not created: {source_file}"
            )

    references = []
    parse_errors = []
    for work_unit in sorted(root.rglob("*.wwu")):
        try:
            work_unit_references = parse_source_references(
                work_unit,
                project_root=root,
            )
        except WwuParseError as exc:
            parse_errors.append(str(exc))
            continue
        references.extend(work_unit_references)
    if parse_errors:
        raise PilotProjectError(
            "Generated Work Unit parsing failed: " + "; ".join(parse_errors)
        )
    inspected_items = []
    for item in items:
        matches = [
            reference
            for reference in references
            if reference.object_name == item.sound_name
            and _source_path_matches(
                reference.source_relative_path,
                item.source_relative_path,
            )
        ]
        if len(matches) != 1:
            raise PilotProjectError(
                "Expected one imported source reference for "
                f"{item.sound_name}, found {len(matches)}"
            )
        inspected_items.append(
            replace(item, work_unit_path=matches[0].work_unit_path)
        )
    return PilotProject(
        project_root=root,
        project_file=project_file,
        object_root=object_root,
        chapter=chapter,
        items=tuple(inspected_items),
    )


def _join_wwise_path(*parts: str) -> str:
    normalized = [part.strip("\\/") for part in parts if part.strip("\\/")]
    return "\\" + "\\".join(normalized)


def _source_path_matches(wwu_path: str, project_relative_path: str) -> bool:
    candidate = wwu_path.strip().replace("\\", "/").lstrip("/").casefold()
    expected = project_relative_path.strip().replace("\\", "/").lstrip("/")
    parts = PurePosixPath(expected).parts
    acceptable = {expected.casefold()}
    if parts and parts[0].casefold() == "originals":
        acceptable.add(PurePosixPath(*parts[1:]).as_posix().casefold())
    if len(parts) >= 4 and tuple(part.casefold() for part in parts[:2]) == (
        "originals",
        "voices",
    ):
        acceptable.add(PurePosixPath(*parts[3:]).as_posix().casefold())
    return candidate in acceptable

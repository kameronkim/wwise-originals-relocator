from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tempfile
import wave

from .wwise_xml import WwuParseError, parse_source_references


class PilotProjectError(RuntimeError):
    """Raised when a disposable Wwise pilot project cannot be created safely."""


@dataclass(frozen=True, slots=True)
class PilotProject:
    project_root: Path
    project_file: Path
    object_root: str
    chapter: str
    sound_name: str
    source_relative_path: str
    target_relative_path: str
    work_unit_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "projectRoot": self.project_root.as_posix(),
            "projectFile": self.project_file.as_posix(),
            "objectRoot": self.object_root,
            "chapter": self.chapter,
            "soundName": self.sound_name,
            "sourceRelativePath": self.source_relative_path,
            "targetRelativePath": self.target_relative_path,
            "workUnitPath": self.work_unit_path,
        }


def create_pilot_project(
    project_root: str | Path,
    *,
    wwise_console: str | Path,
    project_name: str = "WwiseRelocatorPilot",
    platform: str = "Mac",
    language: str = "English(US)",
    object_root: str = r"\Containers\Default Work Unit\VO",
    category: str = "Script",
    chapter: str = "CH04",
    source_category: str = "Scenario",
    sound_name: str = "CH04_S102_WT_001",
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

    project_file = root / f"{project_name}.wproj"
    object_path = _join_wwise_path(object_root, category, chapter, sound_name)
    source_relative_path = PurePosixPath(
        "Originals",
        "Voices",
        language,
        source_category,
        chapter,
        f"{sound_name}.wav",
    ).as_posix()
    target_relative_path = PurePosixPath(
        "Originals",
        "Voices",
        language,
        category,
        chapter,
        f"{sound_name}.wav",
    ).as_posix()

    _run_console(console, "create-new-project", project_file, "--platform", platform)
    with tempfile.TemporaryDirectory(prefix="wwise-relocator-import-") as temporary:
        import_dir = Path(temporary)
        source_wav = import_dir / f"{sound_name}.wav"
        import_file = import_dir / "pilot-import.tsv"
        write_test_tone(source_wav)
        write_import_tsv(
            import_file,
            audio_file=source_wav,
            object_path=object_path,
            originals_subfolder=f"{source_category}\\{chapter}",
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
        sound_name=sound_name,
        source_relative_path=source_relative_path,
        target_relative_path=target_relative_path,
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
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = (
        ("Audio File", "Object Path", "Object Type", "OriginalsSubFolder"),
        (str(Path(audio_file).resolve()), object_path, "Sound Voice", originals_subfolder),
    )
    destination.write_text(
        "\n".join("\t".join(row) for row in rows) + "\n", encoding="utf-8"
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
    sound_name: str,
    source_relative_path: str,
    target_relative_path: str,
) -> PilotProject:
    if not project_file.is_file():
        raise PilotProjectError(f"Wwise project was not created: {project_file}")
    source_file = root / Path(source_relative_path)
    if not source_file.is_file():
        raise PilotProjectError(f"Imported Originals WAV was not created: {source_file}")

    matches = []
    parse_errors = []
    for work_unit in sorted(root.rglob("*.wwu")):
        try:
            references = parse_source_references(work_unit, project_root=root)
        except WwuParseError as exc:
            parse_errors.append(str(exc))
            continue
        matches.extend(
            reference
            for reference in references
            if reference.object_name == sound_name
            and _source_path_matches(
                reference.source_relative_path, source_relative_path
            )
        )
    if parse_errors:
        raise PilotProjectError(
            "Generated Work Unit parsing failed: " + "; ".join(parse_errors)
        )
    if len(matches) != 1:
        raise PilotProjectError(
            f"Expected one imported source reference for {sound_name}, found {len(matches)}"
        )
    return PilotProject(
        project_root=root,
        project_file=project_file,
        object_root=object_root,
        chapter=chapter,
        sound_name=sound_name,
        source_relative_path=source_relative_path,
        target_relative_path=target_relative_path,
        work_unit_path=matches[0].work_unit_path,
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

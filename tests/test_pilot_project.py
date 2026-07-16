from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from wwise_p4_source_relocator.pilot_project import (
    PilotImportRow,
    PilotProjectError,
    _join_wwise_path,
    _source_path_matches,
    _validate_new_project_root,
    create_pilot_project,
    write_import_tsv,
    write_import_tsv_rows,
    write_test_tone,
)


class PilotProjectTests(unittest.TestCase):
    def run_fake_bootstrap(self, project_root: Path, **overrides):
        console = project_root.parent / "WwiseConsole.sh"
        console.touch()
        operations = []
        imported_rows = []

        def fake_run_console(_console, operation, *arguments):
            operations.append(operation)
            project_file = Path(arguments[0])
            if operation == "create-new-project":
                project_file.parent.mkdir(parents=True)
                project_file.write_text("<Project />\n", encoding="utf-8")
                return
            self.assertEqual("tab-delimited-import", operation)
            import_file = Path(arguments[1])
            rows = [
                line.split("\t")
                for line in import_file.read_text(encoding="utf-8").splitlines()[1:]
            ]
            imported_rows.extend(rows)
            sound_elements = []
            for index, (audio_file, _object_path, object_type, subfolder) in enumerate(
                rows,
                start=1,
            ):
                self.assertEqual("Sound Voice", object_type)
                source = Path(audio_file)
                relative_source = (
                    Path("Originals/Voices/English(US)")
                    / Path(subfolder.replace("\\", "/"))
                    / source.name
                )
                destination = project_root / relative_source
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
                sound_name = source.stem
                xml_path = str(relative_source).replace("/", "\\")
                sound_elements.append(
                    f'''<Sound Name="{sound_name}" ID="{{00000000-0000-0000-0000-{index:012d}}}">
  <Children>
    <AudioFileSource Name="{sound_name}" ID="{{10000000-0000-0000-0000-{index:012d}}}">
      <Language>English(US)</Language>
      <AudioFile>{xml_path}</AudioFile>
    </AudioFileSource>
  </Children>
</Sound>'''
                )
            work_unit = (
                project_root
                / "Actor-Mixer Hierarchy"
                / "Default Work Unit.wwu"
            )
            work_unit.parent.mkdir(parents=True, exist_ok=True)
            work_unit.write_text(
                "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
                "<WwiseDocument Type=\"WorkUnit\"><AudioObjects>"
                + "".join(sound_elements)
                + "</AudioObjects></WwiseDocument>\n",
                encoding="utf-8",
            )

        with patch(
            "wwise_p4_source_relocator.pilot_project._run_console",
            side_effect=fake_run_console,
        ):
            pilot = create_pilot_project(
                project_root,
                wwise_console=console,
                **overrides,
            )
        return pilot, operations, imported_rows

    def test_test_tone_is_valid_mono_pcm_wave(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "tone.wav"
            write_test_tone(output)

            with wave.open(str(output), "rb") as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertEqual(audio.getframerate(), 48_000)
                self.assertEqual(audio.getnframes(), 12_000)

    def test_import_tsv_contains_voice_import_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            source.touch()
            output = root / "import.tsv"

            write_import_tsv(
                output,
                audio_file=source,
                object_path=r"\Containers\Default Work Unit\VO\Script\CH04\Line",
                originals_subfolder=r"Scenario\CH04",
            )

            rows = output.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                rows[0], "Audio File\tObject Path\tObject Type\tOriginalsSubFolder"
            )
            self.assertEqual(
                rows[1].split("\t")[1:],
                [
                    r"\Containers\Default Work Unit\VO\Script\CH04\Line",
                    "Sound Voice",
                    r"Scenario\CH04",
                ],
            )

    def test_multi_row_import_tsv_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = tuple(root / f"source-{index}.wav" for index in range(3))
            for source in sources:
                source.touch()
            output = root / "import.tsv"

            write_import_tsv_rows(
                output,
                tuple(
                    PilotImportRow(
                        audio_file=source,
                        object_path=rf"\VO\Type{index}\CH04\Line{index}",
                        originals_subfolder=r"Scenario\CH04",
                    )
                    for index, source in enumerate(sources, start=1)
                ),
            )

            rows = output.read_text(encoding="utf-8").splitlines()
            self.assertEqual(4, len(rows))
            self.assertEqual(
                [r"\VO\Type1\CH04\Line1", r"\VO\Type2\CH04\Line2", r"\VO\Type3\CH04\Line3"],
                [row.split("\t")[1] for row in rows[1:]],
            )

    def test_default_project_creates_three_distinct_misplaced_voice_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "WwiseRelocatorPilot"

            pilot, operations, imported_rows = self.run_fake_bootstrap(root)

            self.assertEqual(
                ["create-new-project", "tab-delimited-import"],
                operations,
            )
            self.assertEqual(3, len(imported_rows))
            self.assertEqual(
                ("Script", "Dialog", "Cutscene"),
                tuple(item.category for item in pilot.items),
            )
            self.assertEqual(
                (
                    "CH04_S102_WT_001",
                    "CH04_S103_DI_001",
                    "CH04_S104_SQ_001",
                ),
                tuple(item.sound_name for item in pilot.items),
            )
            self.assertTrue(
                all("\\VO\\Temp_VO\\" in item.object_path for item in pilot.items)
            )
            self.assertTrue(
                all("/Scenario/CH04/" in item.source_relative_path for item in pilot.items)
            )
            self.assertEqual(
                ("Script", "Dialog", "Cutscene"),
                tuple(
                    Path(item.target_relative_path).parts[-3]
                    for item in pilot.items
                ),
            )
            self.assertTrue(
                all(
                    item.work_unit_path
                    == "Actor-Mixer Hierarchy/Default Work Unit.wwu"
                    for item in pilot.items
                )
            )
            source_bytes = {
                (root / item.source_relative_path).read_bytes()
                for item in pilot.items
            }
            self.assertEqual(3, len(source_bytes))

            metadata = json.loads(
                (root / "relocator-pilot.json").read_text(encoding="utf-8")
            )
            self.assertEqual(2, metadata["schemaVersion"])
            self.assertEqual(3, metadata["itemCount"])
            self.assertEqual(3, len(metadata["items"]))
            self.assertEqual(
                ["Script", "Dialog", "Cutscene"],
                [item["category"] for item in metadata["items"]],
            )

    def test_category_and_sound_name_overrides_create_one_custom_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "WwiseRelocatorPilot"

            pilot, _operations, imported_rows = self.run_fake_bootstrap(
                root,
                category="Dynamic",
                sound_name="CH09_CUSTOM_DY_001",
                chapter="CH09",
            )

            self.assertEqual(1, len(imported_rows))
            self.assertEqual(1, len(pilot.items))
            self.assertEqual("Dynamic", pilot.items[0].category)
            self.assertEqual("CH09_CUSTOM_DY_001", pilot.sound_name)
            self.assertIn("/Scenario/CH09/", pilot.source_relative_path)
            self.assertIn("/Dynamic/CH09/", pilot.target_relative_path)

    def test_project_root_must_not_contain_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "existing.txt").write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(PilotProjectError, "refusing to modify"):
                _validate_new_project_root(root)

    def test_project_folder_must_match_project_name_before_console_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "different-name"

            with self.assertRaisesRegex(PilotProjectError, "folder and .wproj name"):
                create_pilot_project(
                    root,
                    wwise_console=Path(temporary) / "WwiseConsole.sh",
                )

    def test_wwise_path_join_normalizes_separators(self) -> None:
        self.assertEqual(
            _join_wwise_path(
                r"\Containers\Default Work Unit\VO", "Script", "CH04", "Line"
            ),
            r"\Containers\Default Work Unit\VO\Script\CH04\Line",
        )

    def test_real_voice_wwu_path_matches_project_relative_source(self) -> None:
        self.assertTrue(
            _source_path_matches(
                r"Scenario\CH04\Line.wav",
                "Originals/Voices/English(US)/Scenario/CH04/Line.wav",
            )
        )
        self.assertFalse(
            _source_path_matches(
                r"Other\CH04\Line.wav",
                "Originals/Voices/English(US)/Scenario/CH04/Line.wav",
            )
        )


if __name__ == "__main__":
    unittest.main()

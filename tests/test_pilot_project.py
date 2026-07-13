from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import wave

from wwise_p4_source_relocator.pilot_project import (
    PilotProjectError,
    _join_wwise_path,
    _source_path_matches,
    _validate_new_project_root,
    create_pilot_project,
    write_import_tsv,
    write_test_tone,
)


class PilotProjectTests(unittest.TestCase):
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

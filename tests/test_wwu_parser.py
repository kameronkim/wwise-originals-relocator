from pathlib import Path
import tempfile
import unittest

from wwise_p4_source_relocator.wwise_xml import WwuParseError, parse_source_references


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
FIXTURE_WWU = FIXTURE_ROOT / "Actor-Mixer Hierarchy" / "Default Work Unit.wwu"


class ParseSourceReferencesTests(unittest.TestCase):
    def test_reads_audio_file_path_and_object_identity(self) -> None:
        references = parse_source_references(FIXTURE_WWU, project_root=FIXTURE_ROOT)

        self.assertEqual(2, len(references))
        first = references[0]
        self.assertEqual("CH04_S102_WT_001", first.object_name)
        self.assertEqual("{8886C06E-4664-4CEA-B3F1-8668CCDF3683}", first.object_guid)
        self.assertEqual("{99999999-9999-9999-9999-999999999999}", first.source_guid)
        self.assertEqual("English(US)", first.language)
        self.assertEqual(
            "Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav",
            first.source_relative_path,
        )
        self.assertEqual(
            "Actor-Mixer Hierarchy/Default Work Unit.wwu", first.work_unit_path
        )

    def test_rejects_work_unit_outside_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as other_root:
            with self.assertRaisesRegex(WwuParseError, "outside project root"):
                parse_source_references(FIXTURE_WWU, project_root=other_root)

    def test_rejects_sound_without_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.wwu"
            path.write_text(
                "<WwiseDocument><Sound Name='MissingGuid'/></WwiseDocument>",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WwuParseError, "missing its Name or ID"):
                parse_source_references(path)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import tempfile
import unittest

from wwise_p4_source_relocator.wwise_xml import (
    WwuPatchError,
    prepare_source_path_patch,
    source_path_count_for_guid,
    write_prepared_patch,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
FIXTURE_WWU = FIXTURE_ROOT / "Actor-Mixer Hierarchy" / "Default Work Unit.wwu"
GUID = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}"
OLD_PATH = "Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav"
NEW_PATH = "Originals/Voices/English(US)/Script/CH04/CH04_S102_WT_001.wav"


class WwuPatchTests(unittest.TestCase):
    def test_prepares_one_identity_scoped_path_change_without_reformatting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Default Work Unit.wwu"
            original = FIXTURE_WWU.read_bytes().replace(b"\n", b"\r\n")
            path.write_bytes(original)

            patch = prepare_source_path_patch(
                path,
                object_guid=GUID,
                old_relative_path=OLD_PATH,
                new_relative_path=NEW_PATH,
            )
            write_prepared_patch(path, patch)

            self.assertEqual(original.count(b"\r\n"), patch.patched_bytes.count(b"\r\n"))
            self.assertEqual(len(original), len(patch.patched_bytes) + 2)
            self.assertEqual(
                1,
                source_path_count_for_guid(
                    path, object_guid=GUID, relative_path=NEW_PATH
                ),
            )
            self.assertEqual(
                0,
                source_path_count_for_guid(
                    path, object_guid=GUID, relative_path=OLD_PATH
                ),
            )

    def test_rejects_filename_only_match(self) -> None:
        with self.assertRaisesRegex(WwuPatchError, "found 0"):
            prepare_source_path_patch(
                FIXTURE_WWU,
                object_guid=GUID,
                old_relative_path="CH04_S102_WT_001.wav",
                new_relative_path=NEW_PATH,
            )

    def test_preserves_wwu_paths_relative_to_originals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relative.wwu"
            path.write_bytes(
                FIXTURE_WWU.read_bytes().replace(
                    b"Originals\\Voices", b"Voices"
                )
            )

            patch = prepare_source_path_patch(
                path,
                object_guid=GUID,
                old_relative_path=OLD_PATH,
                new_relative_path=NEW_PATH,
            )

            self.assertEqual(
                r"Voices\English(US)\Script\CH04\CH04_S102_WT_001.wav",
                patch.new_xml_path,
            )

    def test_matches_real_voice_path_relative_to_language_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice-relative.wwu"
            path.write_bytes(
                FIXTURE_WWU.read_bytes().replace(
                    b"Originals\\Voices\\English(US)\\", b""
                )
            )

            patch = prepare_source_path_patch(
                path,
                object_guid=GUID,
                old_relative_path=OLD_PATH,
                new_relative_path=NEW_PATH,
            )

            self.assertEqual(
                r"Script\CH04\CH04_S102_WT_001.wav", patch.new_xml_path
            )

    def test_rejects_same_path_appearing_elsewhere_in_work_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.wwu"
            text = FIXTURE_WWU.read_text(encoding="utf-8")
            duplicate = text.replace(
                "</WwiseDocument>",
                "<!-- Originals\\Voices\\English(US)\\Scenario\\CH04\\CH04_S102_WT_001.wav -->\n</WwiseDocument>",
            )
            path.write_text(duplicate, encoding="utf-8")

            with self.assertRaisesRegex(WwuPatchError, "more than once"):
                prepare_source_path_patch(
                    path,
                    object_guid=GUID,
                    old_relative_path=OLD_PATH,
                    new_relative_path=NEW_PATH,
                )

    def test_refuses_to_write_if_work_unit_changed_after_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.wwu"
            path.write_bytes(FIXTURE_WWU.read_bytes())
            patch = prepare_source_path_patch(
                path,
                object_guid=GUID,
                old_relative_path=OLD_PATH,
                new_relative_path=NEW_PATH,
            )
            path.write_bytes(path.read_bytes() + b"\n")

            with self.assertRaisesRegex(WwuPatchError, "changed after"):
                write_prepared_patch(path, patch)


if __name__ == "__main__":
    unittest.main()

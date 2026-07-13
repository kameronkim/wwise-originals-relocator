import os
from pathlib import Path
import unittest

from wwise_p4_source_relocator.waapi_reader import (
    build_scan_result,
    scan_with_connection,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
OBJECT_ROOT = r"\Containers\Default Work Unit\VO\Temp_VO"


def sound_record(name: str, guid: str, category: str = "Script") -> dict[str, object]:
    return {
        "id": guid,
        "name": name,
        "type": "Sound",
        "path": rf"{OBJECT_ROOT}\{category}\CH04\{name}",
        "filePath": str(
            FIXTURE_ROOT / "Actor-Mixer Hierarchy" / "Default Work Unit.wwu"
        ),
    }


def source_record(owner_guid: str, relative_path: str) -> dict[str, object]:
    return {
        "id": "{99999999-9999-9999-9999-999999999999}",
        "name": Path(relative_path).stem,
        "type": "AudioFileSource",
        "owner": {"id": owner_guid, "name": "Sound"},
        "originalRelativeFilePath": relative_path,
        "audioSource:language": "English(US)",
    }


class FakeConnection:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records
        self.calls: list[tuple[object, ...]] = []

    def call(self, uri, args, options):
        self.calls.append((uri, args, options))
        return {"return": self.records}


class WaapiReaderTests(unittest.TestCase):
    def test_builds_scan_items_and_prefixes_originals(self) -> None:
        guid = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}"
        result = build_scan_result(
            [
                sound_record("CH04_S102_WT_001", guid),
                source_record(
                    guid,
                    r"Voices\English(US)\Scenario\CH04\CH04_S102_WT_001.wav",
                ),
            ],
            project_root=FIXTURE_ROOT,
            object_root=OBJECT_ROOT,
            chapter="CH04",
        )

        self.assertEqual(1, len(result.items))
        item = result.items[0]
        self.assertEqual("Script", item.category)
        self.assertEqual("CH04", item.chapter)
        self.assertEqual("English(US)", item.language)
        self.assertEqual(
            ("Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav",),
            item.source_relative_paths,
        )

    def test_scan_uses_read_only_object_get(self) -> None:
        connection = FakeConnection([])

        scan_with_connection(
            connection,
            project_root=FIXTURE_ROOT,
            object_root=OBJECT_ROOT,
            chapter="CH04",
        )

        self.assertEqual(1, len(connection.calls))
        uri, args, options = connection.calls[0]
        self.assertEqual("ak.wwise.core.object.get", uri)
        self.assertEqual({"path": [OBJECT_ROOT]}, args["from"])
        self.assertIn("originalRelativeFilePath", options["return"])

    def test_preserves_multiple_sources_for_manual_review(self) -> None:
        guid = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}"
        result = build_scan_result(
            [
                sound_record("CH04_S102_WT_001", guid),
                source_record(guid, r"Voices\English(US)\Scenario\CH04\one.wav"),
                source_record(guid, r"Voices\English(US)\Scenario\CH04\two.wav"),
            ],
            project_root=FIXTURE_ROOT,
            object_root=OBJECT_ROOT,
            chapter="CH04",
        )

        self.assertEqual(2, len(result.items[0].source_relative_paths))

    @unittest.skipIf(
        os.name == "nt", "Wine-mapped paths apply to non-Windows hosts"
    )
    def test_accepts_real_parent_relation_and_wine_work_unit_path(self) -> None:
        guid = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}"
        sound = sound_record("CH04_S102_WT_001", guid)
        sound["filePath"] = (
            "Z:" + str(FIXTURE_ROOT / "Actor-Mixer Hierarchy" / "Default Work Unit.wwu")
        )
        source = source_record(
            guid, r"Voices\English(US)\Scenario\CH04\CH04_S102_WT_001.wav"
        )
        source["parent"] = source.pop("owner")

        result = build_scan_result(
            [sound, source],
            project_root=FIXTURE_ROOT,
            object_root=OBJECT_ROOT,
            chapter="CH04",
        )

        self.assertEqual(
            "Actor-Mixer Hierarchy/Default Work Unit.wwu",
            result.items[0].work_unit_path,
        )


if __name__ == "__main__":
    unittest.main()

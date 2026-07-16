import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.waapi_reader import (
    WaapiError,
    _run_bounded_live_scan,
    build_scan_result,
    scan_live,
    scan_with_connection,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
OBJECT_ROOT = r"\Containers\Default Work Unit\VO\Temp_VO"


def stalling_scan_worker(*_: object) -> None:
    time.sleep(5)


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
    def test_http_scan_uses_the_detected_rpc_endpoint(self) -> None:
        connection = FakeConnection([])
        with patch(
            "wwise_p4_source_relocator.waapi_reader.HttpWaapiConnection",
            return_value=connection,
        ) as client:
            result = scan_live(
                project_root=FIXTURE_ROOT,
                object_root=OBJECT_ROOT,
                chapter="CH04",
                url="http://127.0.0.1:8090/waapi",
            )

        client.assert_called_once_with(
            "http://127.0.0.1:8090/waapi", timeout=20.0
        )
        self.assertEqual((), result.items)
        self.assertEqual("ak.wwise.core.object.get", connection.calls[0][0])

    def test_bounded_scan_returns_a_timeout_instead_of_hanging(self) -> None:
        with self.assertRaisesRegex(WaapiError, "timed out"):
            _run_bounded_live_scan(
                {},
                timeout_seconds=0.1,
                worker=stalling_scan_worker,
            )

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

    def test_discovers_the_category_parent_below_a_configured_root(self) -> None:
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
            object_root=r"\Containers\Default Work Unit\VO",
            chapter="CH04",
        )

        self.assertEqual(OBJECT_ROOT, result.object_root)
        self.assertEqual(1, len(result.items))
        self.assertEqual("Script", result.items[0].category)

    def test_multiple_object_root_candidates_require_operator_selection(self) -> None:
        first_guid = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}"
        second_guid = "{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}"
        second_sound = sound_record("CH04_S103_DI_001", second_guid, "Dialog")
        second_sound["path"] = second_sound["path"].replace(
            "\\Temp_VO\\", "\\Other_VO\\"
        )

        with self.assertRaisesRegex(WaapiError, "Multiple Wwise object root"):
            build_scan_result(
                [sound_record("CH04_S102_WT_001", first_guid), second_sound],
                project_root=FIXTURE_ROOT,
                object_root=r"\Containers\Default Work Unit\VO",
                chapter="CH04",
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

    @unittest.skipIf(
        os.name == "nt", "Wine-mapped paths apply to non-Windows hosts"
    )
    def test_accepts_wine_home_drive_work_unit_path(self) -> None:
        guid = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}"
        sound = sound_record("CH04_S102_WT_001", guid)
        sound["filePath"] = (
            f"Y:/{FIXTURE_ROOT.name}/Actor-Mixer Hierarchy/Default Work Unit.wwu"
        )

        with patch(
            "wwise_p4_source_relocator.waapi_transport.Path.home",
            return_value=FIXTURE_ROOT.resolve().parent,
        ):
            result = build_scan_result(
                [
                    sound,
                    source_record(
                        guid,
                        r"Voices\English(US)\Scenario\CH04\CH04_S102_WT_001.wav",
                    ),
                ],
                project_root=FIXTURE_ROOT,
                object_root=OBJECT_ROOT,
                chapter="CH04",
            )

        self.assertEqual(
            "Actor-Mixer Hierarchy/Default Work Unit.wwu",
            result.items[0].work_unit_path,
        )

    @unittest.skipIf(
        os.name == "nt", "Wine-mapped paths apply to non-Windows hosts"
    )
    def test_rejects_unrelated_wine_drive_path(self) -> None:
        guid = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}"
        sound = sound_record("CH04_S102_WT_001", guid)
        sound["filePath"] = (
            "Y:/unrelated-project/Actor-Mixer Hierarchy/Default Work Unit.wwu"
        )

        with (
            patch(
                "wwise_p4_source_relocator.waapi_transport.Path.home",
                return_value=FIXTURE_ROOT.resolve().parent,
            ),
            self.assertRaisesRegex(WaapiError, "outside project root"),
        ):
            build_scan_result(
                [
                    sound,
                    source_record(
                        guid,
                        r"Voices\English(US)\Scenario\CH04\CH04_S102_WT_001.wav",
                    ),
                ],
                project_root=FIXTURE_ROOT,
                object_root=OBJECT_ROOT,
                chapter="CH04",
            )

    @unittest.skipIf(
        os.name == "nt", "Wine-mapped paths apply to non-Windows hosts"
    )
    def test_rejects_wine_path_that_escapes_the_project(self) -> None:
        guid = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}"
        sound = sound_record("CH04_S102_WT_001", guid)
        sound["filePath"] = (
            f"Y:/{FIXTURE_ROOT.name}/../outside/Default Work Unit.wwu"
        )

        with (
            patch(
                "wwise_p4_source_relocator.waapi_transport.Path.home",
                return_value=FIXTURE_ROOT.resolve().parent,
            ),
            self.assertRaisesRegex(WaapiError, "outside project root"),
        ):
            build_scan_result(
                [
                    sound,
                    source_record(
                        guid,
                        r"Voices\English(US)\Scenario\CH04\CH04_S102_WT_001.wav",
                    ),
                ],
                project_root=FIXTURE_ROOT,
                object_root=OBJECT_ROOT,
                chapter="CH04",
            )

    @unittest.skipIf(
        os.name == "nt", "Wine-mapped paths apply to non-Windows hosts"
    )
    def test_rejects_unsupported_or_non_absolute_windows_paths(self) -> None:
        guid = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}"
        invalid_paths = (
            "X:/project/Actor-Mixer Hierarchy/Default Work Unit.wwu",
            "Y:Actor-Mixer Hierarchy/Default Work Unit.wwu",
            r"\\server\share\project\Default Work Unit.wwu",
        )

        for file_path in invalid_paths:
            with self.subTest(file_path=file_path):
                sound = sound_record("CH04_S102_WT_001", guid)
                sound["filePath"] = file_path
                with self.assertRaisesRegex(WaapiError, "outside project root"):
                    build_scan_result(
                        [
                            sound,
                            source_record(
                                guid,
                                r"Voices\English(US)\Scenario\CH04\CH04_S102_WT_001.wav",
                            ),
                        ],
                        project_root=FIXTURE_ROOT,
                        object_root=OBJECT_ROOT,
                        chapter="CH04",
                    )


if __name__ == "__main__":
    unittest.main()

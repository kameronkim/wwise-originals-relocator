from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from wwise_p4_source_relocator.applier import ApplyError, apply_single_file
from wwise_p4_source_relocator.models import (
    MoveRecord,
    RelocationPlan,
    RelocationPlanItem,
)
from wwise_p4_source_relocator.p4_client import P4Client, P4Command
from wwise_p4_source_relocator.report import read_rollback_manifest
from wwise_p4_source_relocator.rollback import rollback_manifest
from wwise_p4_source_relocator.validator import validate_live_wwise_manifest


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
OLD_XML_PATH = r"Originals\Voices\English(US)\Scenario\CH04\CH04_S102_WT_001.wav"
NEW_XML_PATH = r"Originals\Voices\English(US)\Script\CH04\CH04_S102_WT_001.wav"


class CleanWorkspaceProbe:
    def is_available(self) -> bool:
        return True

    def is_in_workspace(self, path: Path) -> bool:
        return True

    def is_opened(self, path: Path) -> bool:
        return False


class FakeP4(P4Client):
    def __init__(
        self,
        *,
        unsafe_diff: bool = False,
        required_manifest_path: Path | None = None,
    ) -> None:
        super().__init__(dry_run=False)
        self.unsafe_diff = unsafe_diff
        self.required_manifest_path = required_manifest_path
        self.manifest_prepared_before_mutation = False
        self.original_files: dict[Path, bytes] = {}
        self.calls: list[tuple[str, ...]] = []

    def run(self, command: P4Command) -> subprocess.CompletedProcess[str]:
        self.calls.append(command.argv)
        operation = command.argv[1]
        args = self._file_args(command.argv[2:])
        stdout = ""
        if operation == "edit":
            if self.required_manifest_path is not None:
                self.manifest_prepared_before_mutation = (
                    read_rollback_manifest(self.required_manifest_path).status
                    == "prepared"
                )
            path = Path(args[-1])
            self.original_files.setdefault(path, path.read_bytes())
        elif operation == "move":
            source, target = Path(args[-2]), Path(args[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
        elif operation == "opened":
            stdout = (
                "//depot/source.wav#1 - move/delete change 123 (binary)\n"
                "//depot/target.wav#1 - move/add change 123 (binary)\n"
                "//depot/Default Work Unit.wwu#1 - edit change 123 (text)\n"
            )
        elif operation == "diff":
            extra = "-    <Property Name=\"Volume\"/>\n" if self.unsafe_diff else ""
            stdout = (
                "--- old\n"
                "+++ new\n"
                f"-                  <AudioFile>{OLD_XML_PATH}</AudioFile>\n"
                f"+                  <AudioFile>{NEW_XML_PATH}</AudioFile>\n"
                f"{extra}"
            )
        elif operation == "revert":
            paths = [Path(arg) for arg in args]
            if len(paths) == 2:
                source, target = paths
                if target.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(source)
            for path in paths:
                if path in self.original_files:
                    path.write_bytes(self.original_files[path])
        return subprocess.CompletedProcess(command.argv, 0, stdout=stdout, stderr="")

    @staticmethod
    def _file_args(args: tuple[str, ...]) -> list[str]:
        values = list(args)
        if values[:1] == ["-du"]:
            values = values[1:]
        if values[:1] == ["-c"]:
            values = values[2:]
        return values


class FakeWaapiConnection:
    def __init__(self, record: dict[str, object]) -> None:
        self.record = record

    def call(self, uri, args, options):
        return {"return": [self.record]}


def build_plan(project_root: Path) -> RelocationPlan:
    return RelocationPlan(
        project_root=project_root,
        object_root=r"\Containers\Default Work Unit\VO\Temp_VO",
        chapter="CH04",
        items=(
            RelocationPlanItem(
                object_path=r"\Containers\Default Work Unit\VO\Temp_VO\Script\CH04\CH04_S102_WT_001",
                guid="{8886C06E-4664-4CEA-B3F1-8668CCDF3683}",
                source_file_name="CH04_S102_WT_001.wav",
                from_relative_path="Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav",
                to_relative_path="Originals/Voices/English(US)/Script/CH04/CH04_S102_WT_001.wav",
                work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
                action="move-and-patch",
            ),
        ),
    )


class ApplyRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp.name) / "WwiseProject"
        shutil.copytree(FIXTURE_ROOT, self.project_root)
        self.manifest_path = Path(self.temp.name) / "manifest.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_applies_one_file_then_rolls_back_only_manifest_paths(self) -> None:
        p4 = FakeP4(required_manifest_path=self.manifest_path)

        manifest, validation = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )

        source = self.project_root / manifest.moves[0].from_relative_path
        target = self.project_root / manifest.moves[0].to_relative_path
        self.assertTrue(validation.is_valid)
        self.assertTrue(p4.manifest_prepared_before_mutation)
        mutation_operations = [call[1] for call in p4.calls[:3]]
        self.assertEqual(["edit", "edit", "move"], mutation_operations)
        self.assertEqual("applied", manifest.status)
        self.assertFalse(source.exists())
        self.assertTrue(target.is_file())
        self.assertEqual("applied", read_rollback_manifest(self.manifest_path).status)

        live = validate_live_wwise_manifest(
            manifest,
            connection=FakeWaapiConnection(
                {
                    "id": manifest.affected_objects[0].guid,
                    "path": manifest.affected_objects[0].object_path,
                    "originalRelativeFilePath": "Voices/English(US)/Script/CH04/CH04_S102_WT_001.wav",
                    "originalFilePath": str(target),
                }
            ),
        )
        self.assertTrue(live.is_valid)

        rollback = rollback_manifest(
            manifest, p4=p4, manifest_path=self.manifest_path
        )

        self.assertTrue(rollback.is_valid)
        self.assertTrue(source.is_file())
        self.assertFalse(target.exists())
        self.assertEqual(
            "rolled-back", read_rollback_manifest(self.manifest_path).status
        )
        revert_calls = [call for call in p4.calls if call[1] == "revert"]
        self.assertEqual(2, len(revert_calls))
        self.assertTrue(all("//..." not in call for call in revert_calls))

    def test_unsafe_post_apply_diff_triggers_automatic_rollback(self) -> None:
        p4 = FakeP4(unsafe_diff=True)
        plan = build_plan(self.project_root)

        with self.assertRaisesRegex(ApplyError, "Post-apply validation failed"):
            apply_single_file(
                plan,
                only="CH04_S102_WT_001.wav",
                changelist="123",
                manifest_path=self.manifest_path,
                p4=p4,
                probe=CleanWorkspaceProbe(),
            )

        source = self.project_root / plan.items[0].from_relative_path
        target = self.project_root / plan.items[0].to_relative_path
        self.assertTrue(source.is_file())
        self.assertFalse(target.exists())
        self.assertEqual(
            "rolled-back", read_rollback_manifest(self.manifest_path).status
        )

    def test_only_must_select_exactly_one_move_candidate(self) -> None:
        with self.assertRaisesRegex(ApplyError, "found 0"):
            apply_single_file(
                build_plan(self.project_root),
                only="different.wav",
                changelist=None,
                manifest_path=self.manifest_path,
                p4=FakeP4(),
                probe=CleanWorkspaceProbe(),
            )
        self.assertFalse(self.manifest_path.exists())

    def test_live_validation_detects_stale_wwise_identity_and_source(self) -> None:
        p4 = FakeP4()
        manifest, _ = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )

        result = validate_live_wwise_manifest(
            manifest,
            connection=FakeWaapiConnection(
                {
                    "id": "{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}",
                    "path": r"\Containers\Wrong",
                    "originalRelativeFilePath": "Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav",
                    "originalFilePath": str(self.project_root / "missing.wav"),
                }
            ),
        )

        self.assertEqual(
            {
                "wwise-guid-changed",
                "wwise-path-changed",
                "wwise-source-mismatch",
                "wwise-source-missing",
            },
            {issue.code for issue in result.issues},
        )

    def test_live_validation_accepts_wine_mapped_original_path(self) -> None:
        p4 = FakeP4()
        manifest, _ = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )
        affected = manifest.affected_objects[0]
        target = manifest.project_root / manifest.moves[0].to_relative_path

        result = validate_live_wwise_manifest(
            manifest,
            connection=FakeWaapiConnection(
                {
                    "id": affected.guid,
                    "path": affected.object_path,
                    "originalRelativeFilePath": affected.after_source_relative_path,
                    "originalFilePath": "Z:" + str(target),
                }
            ),
        )

        self.assertTrue(result.is_valid)

    def test_rollback_rejects_manifest_path_outside_project(self) -> None:
        p4 = FakeP4()
        manifest, _ = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )
        calls_before_rollback = len(p4.calls)
        tampered = replace(
            manifest,
            moves=(MoveRecord("../outside.wav", "../outside-target.wav"),),
        )

        result = rollback_manifest(tampered, p4=p4)

        self.assertEqual("outside-project", result.issues[0].code)
        self.assertEqual(calls_before_rollback, len(p4.calls))

    def test_rollback_rejects_empty_manifest_without_running_p4(self) -> None:
        p4 = FakeP4()
        manifest, _ = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )
        calls_before_rollback = len(p4.calls)

        result = rollback_manifest(replace(manifest, moves=()), p4=p4)

        self.assertEqual("manifest-scope", result.issues[0].code)
        self.assertEqual(calls_before_rollback, len(p4.calls))


if __name__ == "__main__":
    unittest.main()

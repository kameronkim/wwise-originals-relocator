from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.applier import (
    ApplyError,
    apply_selected_files,
    apply_single_file,
)
from wwise_p4_source_relocator.models import (
    MoveRecord,
    RelocationPlan,
    RelocationPlanItem,
)
from wwise_p4_source_relocator.p4_client import P4Client, P4Command
from wwise_p4_source_relocator.report import read_rollback_manifest
from wwise_p4_source_relocator.rollback import rollback_manifest
from wwise_p4_source_relocator.validator import (
    validate_applied_manifest,
    validate_live_wwise_manifest,
    validate_live_wwise_manifest_at_url,
)


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

    def has_local_changes(self, path: Path) -> bool:
        return False


class FakeP4(P4Client):
    def __init__(
        self,
        *,
        unsafe_diff: bool = False,
        required_manifest_path: Path | None = None,
        fail_move_number: int | None = None,
    ) -> None:
        super().__init__(dry_run=False)
        self.unsafe_diff = unsafe_diff
        self.required_manifest_path = required_manifest_path
        self.fail_move_number = fail_move_number
        self.move_count = 0
        self.manifest_prepared_before_mutation = False
        self.original_files: dict[Path, bytes] = {}
        self.opened_files: dict[Path, dict[str, str]] = {}
        self.calls: list[tuple[str, ...]] = []

    def run(self, command: P4Command) -> subprocess.CompletedProcess[str]:
        self.calls.append(command.argv)
        operation = command.argv[1]
        command_args = command.argv[2:]
        changelist = self._changelist(command_args)
        args = self._file_args(command_args)
        stdout = ""
        if operation == "edit":
            if self.required_manifest_path is not None:
                self.manifest_prepared_before_mutation = (
                    read_rollback_manifest(self.required_manifest_path).status
                    == "prepared"
                )
            path = Path(args[-1])
            self.original_files.setdefault(path, path.read_bytes())
            self.opened_files[path] = {
                "depotFile": self._depot_path(path),
                "clientFile": str(path),
                "action": "edit",
                "change": changelist,
            }
        elif operation == "move":
            self.move_count += 1
            if self.move_count == self.fail_move_number:
                raise subprocess.CalledProcessError(1, command.argv)
            source, target = Path(args[-2]), Path(args[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            source_depot = self._depot_path(source)
            target_depot = self._depot_path(target)
            self.opened_files[source] = {
                "depotFile": source_depot,
                "clientFile": str(source),
                "action": "move/delete",
                "change": changelist,
                "movedFile": target_depot,
            }
            self.opened_files[target] = {
                "depotFile": target_depot,
                "clientFile": str(target),
                "action": "move/add",
                "change": changelist,
                "movedFile": source_depot,
            }
        elif operation == "fstat":
            records = [
                self.opened_files[Path(value)]
                for value in self._fstat_paths(command_args)
                if Path(value) in self.opened_files
            ]
            stdout = "\n\n".join(
                "\n".join(f"... {key} {value}" for key, value in record.items())
                for record in records
            )
            if stdout:
                stdout += "\n"
        elif operation == "opened":
            selected_paths = {Path(arg) for arg in args}
            records = [
                record
                for path, record in self.opened_files.items()
                if record["change"] == changelist
                and (not selected_paths or path in selected_paths)
            ]
            lines = [
                f"{record['depotFile']}#1 - {record['action']} change "
                f"{record['change']} (text)"
                for record in records
            ]
            stdout = "\n".join(lines) + "\n"
        elif operation == "diff":
            extra = "-    <Property Name=\"Volume\"/>\n" if self.unsafe_diff else ""
            work_unit = Path(args[-1])
            original = self.original_files[work_unit].decode("utf-8")
            current = work_unit.read_text(encoding="utf-8")
            removed = [
                line for line in original.splitlines() if line not in current.splitlines()
            ]
            added = [
                line for line in current.splitlines() if line not in original.splitlines()
            ]
            stdout = "\n".join(
                ["--- old", "+++ new"]
                + [f"-{line}" for line in removed]
                + [f"+{line}" for line in added]
            ) + f"\n{extra}"
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
                self.opened_files.pop(path, None)
        return subprocess.CompletedProcess(command.argv, 0, stdout=stdout, stderr="")

    @staticmethod
    def _file_args(args: tuple[str, ...]) -> list[str]:
        values = list(args)
        if values[:1] == ["-du"]:
            values = values[1:]
        if values[:1] == ["-c"]:
            values = values[2:]
        return values

    @staticmethod
    def _changelist(args: tuple[str, ...]) -> str:
        values = list(args)
        if "-c" in values:
            return values[values.index("-c") + 1]
        return "default"

    @staticmethod
    def _fstat_paths(args: tuple[str, ...]) -> list[str]:
        values = list(args)
        return values[values.index("-T") + 2 :]

    @staticmethod
    def _depot_path(path: Path) -> str:
        return f"//depot{path.as_posix()}"


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


def build_batch_plan(project_root: Path) -> RelocationPlan:
    first = build_plan(project_root).items[0]
    second = RelocationPlanItem(
        object_path=r"\Containers\Default Work Unit\VO\Temp_VO\Script\CH04\CH04_D001_WT_001",
        guid="{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}",
        source_file_name="CH04_D001_WT_001.wav",
        from_relative_path="Originals/Voices/English(US)/Dialog/CH04/CH04_D001_WT_001.wav",
        to_relative_path="Originals/Voices/English(US)/Script/CH04/CH04_D001_WT_001.wav",
        work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
        action="move-and-patch",
    )
    return RelocationPlan(
        project_root=project_root,
        object_root=r"\Containers\Default Work Unit\VO\Temp_VO",
        chapter="CH04",
        items=(first, second),
    )


class ApplyRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp.name) / "WwiseProject"
        shutil.copytree(FIXTURE_ROOT, self.project_root)
        second_source = (
            self.project_root
            / "Originals/Voices/English(US)/Dialog/CH04/CH04_D001_WT_001.wav"
        )
        second_source.parent.mkdir(parents=True, exist_ok=True)
        second_source.write_bytes(b"RIFF-second-wave")
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
        target = manifest.project_root / manifest.moves[0].to_relative_path
        self.assertTrue(validation.is_valid)
        self.assertEqual(
            {
                "changelist": "123",
                "moves": 1,
                "workUnits": 1,
                "files": 3,
            },
            {
                "changelist": validation.details["perforce"]["changelist"],
                "moves": validation.details["perforce"]["movePairCount"],
                "workUnits": validation.details["perforce"]["workUnitEditCount"],
                "files": validation.details["perforce"]["actualFileCount"],
            },
        )
        self.assertTrue(p4.manifest_prepared_before_mutation)
        mutation_operations = [call[1] for call in p4.calls[:3]]
        self.assertEqual(["edit", "edit", "move"], mutation_operations)
        self.assertEqual("awaiting-wwise-reload", manifest.status)
        self.assertFalse(source.exists())
        self.assertTrue(target.is_file())
        self.assertEqual(
            "awaiting-wwise-reload",
            read_rollback_manifest(self.manifest_path).status,
        )

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

    def test_applies_and_rolls_back_multiple_files_in_one_manifest(self) -> None:
        p4 = FakeP4(required_manifest_path=self.manifest_path)
        plan = build_batch_plan(self.project_root)

        manifest, validation = apply_selected_files(
            plan,
            only=("CH04_S102_WT_001.wav", "CH04_D001_WT_001.wav"),
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )

        self.assertTrue(validation.is_valid)
        self.assertEqual(2, len(manifest.moves))
        self.assertEqual(2, len(manifest.patched_files))
        self.assertEqual(
            1,
            len({record.patched_sha256 for record in manifest.patched_files}),
        )
        self.assertTrue(
            all(
                (self.project_root / move.to_relative_path).is_file()
                for move in manifest.moves
            )
        )

        result = rollback_manifest(
            manifest, p4=p4, manifest_path=self.manifest_path
        )

        self.assertTrue(result.is_valid)
        self.assertTrue(
            all(
                (self.project_root / move.from_relative_path).is_file()
                for move in manifest.moves
            )
        )
        self.assertEqual(
            1,
            len(
                [
                    call
                    for call in p4.calls
                    if call[1] == "revert"
                    and call[-1].endswith("Default Work Unit.wwu")
                ]
            ),
        )

    def test_validation_accepts_an_exact_default_changelist(self) -> None:
        p4 = FakeP4()

        _, validation = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist=None,
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )

        perforce = validation.details["perforce"]
        self.assertTrue(validation.is_valid)
        self.assertEqual("default", perforce["changelist"])
        self.assertTrue(perforce["isDefault"])

    def test_validation_rejects_a_wrong_perforce_action(self) -> None:
        p4 = FakeP4()
        manifest, _ = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )
        target = manifest.project_root / manifest.moves[0].to_relative_path
        p4.opened_files[target]["action"] = "add"

        validation = validate_applied_manifest(manifest, p4=p4)

        self.assertIn("p4-action-mismatch", self._issue_codes(validation))

    def test_validation_rejects_a_broken_move_pair(self) -> None:
        p4 = FakeP4()
        manifest, _ = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )
        target = manifest.project_root / manifest.moves[0].to_relative_path
        p4.opened_files[target]["movedFile"] = "//depot/unrelated.wav"

        validation = validate_applied_manifest(manifest, p4=p4)

        self.assertIn("p4-move-pair-mismatch", self._issue_codes(validation))

    def test_validation_rejects_a_file_in_another_changelist(self) -> None:
        p4 = FakeP4()
        manifest, _ = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )
        target = manifest.project_root / manifest.moves[0].to_relative_path
        p4.opened_files[target]["change"] = "999"

        validation = validate_applied_manifest(manifest, p4=p4)

        self.assertIn("p4-changelist-mismatch", self._issue_codes(validation))
        self.assertIn("p4-changelist-missing-files", self._issue_codes(validation))

    def test_validation_rejects_unrelated_files_in_the_changelist(self) -> None:
        p4 = FakeP4()
        manifest, _ = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )
        unrelated = self.project_root / "unrelated.asset"
        p4.opened_files[unrelated] = {
            "depotFile": p4._depot_path(unrelated),
            "clientFile": str(unrelated),
            "action": "edit",
            "change": "123",
        }

        validation = validate_applied_manifest(manifest, p4=p4)

        self.assertIn("p4-changelist-extra-files", self._issue_codes(validation))
        self.assertEqual(
            1, validation.details["perforce"]["unexpectedFileCount"]
        )

    @staticmethod
    def _issue_codes(validation) -> set[str]:
        return {issue.code for issue in validation.issues}

    def test_batch_failure_rolls_back_already_moved_files_in_reverse(self) -> None:
        p4 = FakeP4(fail_move_number=2)
        plan = build_batch_plan(self.project_root)

        with self.assertRaisesRegex(ApplyError, "Apply failed"):
            apply_selected_files(
                plan,
                only=("CH04_S102_WT_001.wav", "CH04_D001_WT_001.wav"),
                changelist="123",
                manifest_path=self.manifest_path,
                p4=p4,
                probe=CleanWorkspaceProbe(),
            )

        self.assertTrue(
            all(
                (self.project_root / item.from_relative_path).is_file()
                for item in plan.items
            )
        )
        self.assertTrue(
            all(
                not (self.project_root / item.to_relative_path).exists()
                for item in plan.items
            )
        )
        recovery = read_rollback_manifest(self.manifest_path)
        self.assertEqual("rolled-back", recovery.status)
        self.assertEqual(1, len(recovery.moves))

    def test_batch_rejects_conflicting_targets_before_mutation(self) -> None:
        plan = build_batch_plan(self.project_root)
        conflicting = replace(
            plan,
            items=(
                plan.items[0],
                replace(
                    plan.items[1],
                    to_relative_path=plan.items[0].to_relative_path,
                ),
            ),
        )
        p4 = FakeP4()

        with self.assertRaisesRegex(ApplyError, "duplicate source or target"):
            apply_selected_files(
                conflicting,
                only=("CH04_S102_WT_001.wav", "CH04_D001_WT_001.wav"),
                changelist="123",
                manifest_path=self.manifest_path,
                p4=p4,
                probe=CleanWorkspaceProbe(),
            )

        self.assertEqual([], p4.calls)
        self.assertFalse(self.manifest_path.exists())

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

    def test_rollback_hash_mismatch_reports_expected_and_actual_hashes(self) -> None:
        p4 = FakeP4()
        manifest, _ = apply_single_file(
            build_plan(self.project_root),
            only="CH04_S102_WT_001.wav",
            changelist="123",
            manifest_path=self.manifest_path,
            p4=p4,
            probe=CleanWorkspaceProbe(),
        )
        work_unit = next(
            path for path in p4.original_files if path.suffix.casefold() == ".wwu"
        )
        p4.original_files[work_unit] = b"different restored bytes"

        result = rollback_manifest(
            manifest, p4=p4, manifest_path=self.manifest_path
        )

        issue = next(
            issue for issue in result.issues if issue.code == "rollback-wwu-mismatch"
        )
        self.assertIn("expected=", issue.message)
        self.assertIn("actual=", issue.message)

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

    def test_live_validation_uses_http_waapi_endpoint(self) -> None:
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
        connection = FakeWaapiConnection(
            {
                "id": affected.guid,
                "path": affected.object_path,
                "originalRelativeFilePath": affected.after_source_relative_path,
                "originalFilePath": str(target),
            }
        )

        with patch(
            "wwise_p4_source_relocator.validator.HttpWaapiConnection",
            return_value=connection,
        ) as client:
            result = validate_live_wwise_manifest_at_url(
                manifest,
                url="http://127.0.0.1:8090/waapi",
            )

        self.assertTrue(result.is_valid)
        client.assert_called_once_with(
            "http://127.0.0.1:8090/waapi", timeout=20.0
        )

    @unittest.skipIf(
        os.name == "nt", "Wine-mapped paths apply to non-Windows hosts"
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

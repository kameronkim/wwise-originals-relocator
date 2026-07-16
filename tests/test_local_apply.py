import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.applier import (
    ApplyError,
    apply_selected_files_locally,
)
from wwise_p4_source_relocator.cli import main
from wwise_p4_source_relocator.file_ops import move_file_no_replace
from wwise_p4_source_relocator.models import (
    RelocationPlan,
    RelocationPlanItem,
    RollbackManifest,
    ValidationResult,
)
from wwise_p4_source_relocator.operation_lock import (
    ProjectOperationBusyError,
    project_operation_lock,
)
from wwise_p4_source_relocator.report import read_rollback_manifest
from wwise_p4_source_relocator.rollback import rollback_local_manifest
from wwise_p4_source_relocator.validator import (
    validate_applied_filesystem_manifest,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


def build_plan(project_root: Path, *, batch: bool = False) -> RelocationPlan:
    items = [
        RelocationPlanItem(
            object_path=(
                r"\Containers\Default Work Unit\VO\Temp_VO\Script\CH04"
                r"\CH04_S102_WT_001"
            ),
            guid="{8886C06E-4664-4CEA-B3F1-8668CCDF3683}",
            source_file_name="CH04_S102_WT_001.wav",
            from_relative_path=(
                "Originals/Voices/English(US)/Scenario/CH04/"
                "CH04_S102_WT_001.wav"
            ),
            to_relative_path=(
                "Originals/Voices/English(US)/Script/CH04/"
                "CH04_S102_WT_001.wav"
            ),
            work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
            action="move-and-patch",
        )
    ]
    if batch:
        items.append(
            RelocationPlanItem(
                object_path=(
                    r"\Containers\Default Work Unit\VO\Temp_VO\Script\CH04"
                    r"\CH04_D001_WT_001"
                ),
                guid="{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}",
                source_file_name="CH04_D001_WT_001.wav",
                from_relative_path=(
                    "Originals/Voices/English(US)/Dialog/CH04/"
                    "CH04_D001_WT_001.wav"
                ),
                to_relative_path=(
                    "Originals/Voices/English(US)/Script/CH04/"
                    "CH04_D001_WT_001.wav"
                ),
                work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
                action="move-and-patch",
            )
        )
    return RelocationPlan(
        project_root=project_root,
        object_root=r"\Containers\Default Work Unit\VO\Temp_VO",
        chapter="CH04",
        items=tuple(items),
    )


class LocalApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project_root = self.root / "WwiseProject"
        shutil.copytree(FIXTURE_ROOT, self.project_root)
        second_source = (
            self.project_root
            / "Originals/Voices/English(US)/Dialog/CH04/CH04_D001_WT_001.wav"
        )
        second_source.parent.mkdir(parents=True, exist_ok=True)
        second_source.write_bytes(b"RIFF-second-wave")
        self.manifest_path = self.root / "rollback-manifest.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_local_apply_writes_manifest_before_move_and_rolls_back(self) -> None:
        observed_statuses: list[str] = []

        def checked_move(path: Path, target: Path) -> None:
            observed_statuses.append(
                read_rollback_manifest(self.manifest_path).status
            )
            move_file_no_replace(path, target)

        with patch(
            "wwise_p4_source_relocator.applier.move_file_no_replace",
            side_effect=checked_move,
        ):
            manifest, validation = apply_selected_files_locally(
                build_plan(self.project_root),
                only=("CH04_S102_WT_001.wav",),
                manifest_path=self.manifest_path,
            )

        self.assertEqual(["prepared"], observed_statuses)
        self.assertTrue(validation.is_valid)
        self.assertEqual("local-filesystem", manifest.operation_mode)
        self.assertIsNotNone(manifest.moves[0].source_sha256)
        stored_manifest = read_rollback_manifest(self.manifest_path)
        self.assertEqual(
            (
                "Originals/Voices/English(US)/Script",
                "Originals/Voices/English(US)/Script/CH04",
            ),
            stored_manifest.created_directories,
        )
        self.assertTrue(validate_applied_filesystem_manifest(manifest).is_valid)
        source = self.project_root / manifest.moves[0].from_relative_path
        target = self.project_root / manifest.moves[0].to_relative_path
        self.assertFalse(source.exists())
        self.assertTrue(target.is_file())

        rolled_back = rollback_local_manifest(
            stored_manifest,
            manifest_path=self.manifest_path,
        )

        self.assertTrue(rolled_back.is_valid)
        self.assertTrue(source.is_file())
        self.assertFalse(target.exists())
        self.assertFalse(target.parent.exists())
        self.assertFalse(target.parent.parent.exists())
        self.assertEqual(
            "rolled-back",
            read_rollback_manifest(self.manifest_path).status,
        )

    def test_local_apply_failure_automatically_restores_moved_wav(self) -> None:
        plan = build_plan(self.project_root)
        source = self.project_root / plan.items[0].from_relative_path
        target = self.project_root / plan.items[0].to_relative_path

        with patch(
            "wwise_p4_source_relocator.applier.write_prepared_patch",
            side_effect=OSError("write stopped"),
        ):
            with self.assertRaisesRegex(ApplyError, "Apply failed"):
                apply_selected_files_locally(
                    plan,
                    only=("CH04_S102_WT_001.wav",),
                    manifest_path=self.manifest_path,
                )

        self.assertTrue(source.is_file())
        self.assertFalse(target.exists())
        self.assertEqual(
            "rolled-back",
            read_rollback_manifest(self.manifest_path).status,
        )

    def test_atomic_work_unit_replace_failure_preserves_original_project(self) -> None:
        plan = build_plan(self.project_root)
        source = self.project_root / plan.items[0].from_relative_path
        target = self.project_root / plan.items[0].to_relative_path
        work_unit = self.project_root / plan.items[0].work_unit_path
        original_work_unit = work_unit.read_bytes()
        original_wave = source.read_bytes()

        with patch(
            "wwise_p4_source_relocator.wwise_xml._replace_file",
            side_effect=PermissionError("Work Unit is locked"),
        ):
            with self.assertRaisesRegex(ApplyError, "Apply failed"):
                apply_selected_files_locally(
                    plan,
                    only=("CH04_S102_WT_001.wav",),
                    manifest_path=self.manifest_path,
                )

        self.assertEqual(original_work_unit, work_unit.read_bytes())
        self.assertEqual(original_wave, source.read_bytes())
        self.assertFalse(target.exists())
        self.assertEqual(
            [],
            list(work_unit.parent.glob(f".{work_unit.name}.*.tmp")),
        )
        self.assertEqual(
            "rolled-back",
            read_rollback_manifest(self.manifest_path).status,
        )

    def test_local_apply_never_replaces_a_target_created_during_move(self) -> None:
        plan = build_plan(self.project_root)
        source = self.project_root / plan.items[0].from_relative_path
        target = self.project_root / plan.items[0].to_relative_path
        original_wave = source.read_bytes()
        unrelated = b"unrelated-target"

        def race_target(source_path: Path, target_path: Path) -> None:
            target_path.write_bytes(unrelated)
            move_file_no_replace(source_path, target_path)

        with patch(
            "wwise_p4_source_relocator.applier.move_file_no_replace",
            side_effect=race_target,
        ):
            with self.assertRaises(ApplyError):
                apply_selected_files_locally(
                    plan,
                    only=("CH04_S102_WT_001.wav",),
                    manifest_path=self.manifest_path,
                )

        self.assertEqual(original_wave, source.read_bytes())
        self.assertEqual(unrelated, target.read_bytes())
        self.assertEqual(
            "failed",
            read_rollback_manifest(self.manifest_path).status,
        )

    def test_interrupted_manifest_status_write_keeps_prepared_recovery(self) -> None:
        from wwise_p4_source_relocator import report

        original_replace = report._replace_file
        replace_count = 0

        def interrupt_second_replace(source: Path, target: Path) -> None:
            nonlocal replace_count
            replace_count += 1
            if replace_count == 2:
                raise KeyboardInterrupt("status update interrupted")
            original_replace(source, target)

        with patch(
            "wwise_p4_source_relocator.report._replace_file",
            side_effect=interrupt_second_replace,
        ):
            with self.assertRaises(KeyboardInterrupt):
                apply_selected_files_locally(
                    build_plan(self.project_root),
                    only=("CH04_S102_WT_001.wav",),
                    manifest_path=self.manifest_path,
                )

        manifest = read_rollback_manifest(self.manifest_path)
        self.assertEqual("prepared", manifest.status)
        self.assertTrue(
            (self.project_root / manifest.moves[0].to_relative_path).is_file()
        )
        self.assertTrue(
            rollback_local_manifest(
                manifest,
                manifest_path=self.manifest_path,
            ).is_valid
        )

    def test_prepared_manifest_recovers_crash_between_link_and_unlink(self) -> None:
        def crash_after_link(source: Path, target: Path) -> None:
            os.link(source, target)
            raise KeyboardInterrupt("simulated process stop")

        with patch(
            "wwise_p4_source_relocator.applier.move_file_no_replace",
            side_effect=crash_after_link,
        ):
            with self.assertRaises(KeyboardInterrupt):
                apply_selected_files_locally(
                    build_plan(self.project_root),
                    only=("CH04_S102_WT_001.wav",),
                    manifest_path=self.manifest_path,
                )

        manifest = read_rollback_manifest(self.manifest_path)
        source = self.project_root / manifest.moves[0].from_relative_path
        target = self.project_root / manifest.moves[0].to_relative_path
        self.assertTrue(source.samefile(target))

        result = rollback_local_manifest(
            manifest,
            manifest_path=self.manifest_path,
        )

        self.assertTrue(result.is_valid)
        self.assertTrue(source.is_file())
        self.assertFalse(target.exists())

    def test_concurrent_local_apply_cannot_interfere_with_in_progress_move(
        self,
    ) -> None:
        plan = build_plan(self.project_root)
        source = self.project_root / plan.items[0].from_relative_path
        target = self.project_root / plan.items[0].to_relative_path
        linked = threading.Event()
        release = threading.Event()
        first_errors: list[BaseException] = []

        def paused_move(source_path: Path, target_path: Path) -> None:
            os.link(source_path, target_path)
            linked.set()
            if not release.wait(timeout=5):
                raise RuntimeError("concurrency test timed out")
            source_path.unlink()

        def first_apply() -> None:
            try:
                apply_selected_files_locally(
                    plan,
                    only=("CH04_S102_WT_001.wav",),
                    manifest_path=self.manifest_path,
                )
            except BaseException as exc:
                first_errors.append(exc)

        with patch(
            "wwise_p4_source_relocator.applier.move_file_no_replace",
            side_effect=paused_move,
        ):
            worker = threading.Thread(target=first_apply)
            worker.start()
            self.assertTrue(linked.wait(timeout=5))
            self.assertTrue(source.samefile(target))
            with self.assertRaisesRegex(ApplyError, "already running"):
                apply_selected_files_locally(
                    plan,
                    only=("CH04_S102_WT_001.wav",),
                    manifest_path=self.root / "second-manifest.json",
                )
            release.set()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual([], first_errors)
        self.assertFalse(source.exists())
        self.assertTrue(target.is_file())
        self.assertFalse((self.root / "second-manifest.json").exists())

    def test_project_operation_lock_is_shared_between_processes(self) -> None:
        ready = self.root / "lock-ready"
        release = self.root / "lock-release"
        source_root = Path(__file__).parents[1] / "src"
        script = (
            "import sys,time; from pathlib import Path; "
            "sys.path.insert(0, sys.argv[1]); "
            "from wwise_p4_source_relocator.operation_lock import "
            "project_operation_lock; "
            "root,ready,release=map(Path,sys.argv[2:5]); "
            "\nwith project_operation_lock(root):\n"
            " ready.write_text('ready', encoding='utf-8')\n"
            " while not release.exists(): time.sleep(0.01)\n"
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(source_root),
                str(self.project_root),
                str(ready),
                str(release),
            ]
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("child process did not acquire the project lock")
                time.sleep(0.01)
            self.assertIsNone(process.poll())
            with self.assertRaises(ProjectOperationBusyError):
                with project_operation_lock(self.project_root):
                    pass
        finally:
            release.write_text("release", encoding="utf-8")
            process.wait(timeout=5)

        self.assertEqual(0, process.returncode)

    def test_project_operation_lock_uses_filesystem_identity_for_case_aliases(
        self,
    ) -> None:
        alias = self.project_root.with_name(self.project_root.name.swapcase())
        try:
            aliases_same_project = self.project_root.samefile(alias)
        except OSError:
            aliases_same_project = False
        if not aliases_same_project:
            self.skipTest("filesystem is case-sensitive for this project path")

        attempts: list[str] = []

        def acquire_alias() -> None:
            try:
                with project_operation_lock(alias):
                    attempts.append("acquired")
            except ProjectOperationBusyError:
                attempts.append("busy")

        with project_operation_lock(self.project_root):
            worker = threading.Thread(target=acquire_alias)
            worker.start()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(["busy"], attempts)

    def test_local_rollback_never_replaces_a_source_created_during_move(self) -> None:
        manifest, _ = apply_selected_files_locally(
            build_plan(self.project_root),
            only=("CH04_S102_WT_001.wav",),
            manifest_path=self.manifest_path,
        )
        source = self.project_root / manifest.moves[0].from_relative_path
        target = self.project_root / manifest.moves[0].to_relative_path
        moved_wave = target.read_bytes()
        unrelated = b"unrelated-source"

        def race_source(target_path: Path, source_path: Path) -> None:
            source_path.write_bytes(unrelated)
            move_file_no_replace(target_path, source_path)

        with patch(
            "wwise_p4_source_relocator.rollback.move_file_no_replace",
            side_effect=race_source,
        ):
            result = rollback_local_manifest(
                manifest,
                manifest_path=self.manifest_path,
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(unrelated, source.read_bytes())
        self.assertEqual(moved_wave, target.read_bytes())
        self.assertEqual(
            "failed",
            read_rollback_manifest(self.manifest_path).status,
        )

    def test_local_rollback_refuses_wav_drift_before_mutation(self) -> None:
        manifest, _ = apply_selected_files_locally(
            build_plan(self.project_root),
            only=("CH04_S102_WT_001.wav",),
            manifest_path=self.manifest_path,
        )
        source = self.project_root / manifest.moves[0].from_relative_path
        target = self.project_root / manifest.moves[0].to_relative_path
        work_unit = self.project_root / manifest.patched_files[0].relative_path
        patched_work_unit = work_unit.read_bytes()
        target.write_bytes(b"changed-after-apply")

        result = rollback_local_manifest(
            manifest,
            manifest_path=self.manifest_path,
        )

        self.assertFalse(result.is_valid)
        self.assertIn("rollback-wav-drift", {issue.code for issue in result.issues})
        self.assertFalse(source.exists())
        self.assertEqual(b"changed-after-apply", target.read_bytes())
        self.assertEqual(patched_work_unit, work_unit.read_bytes())

    def test_local_rollback_refuses_work_unit_drift_before_mutation(self) -> None:
        manifest, _ = apply_selected_files_locally(
            build_plan(self.project_root),
            only=("CH04_S102_WT_001.wav",),
            manifest_path=self.manifest_path,
        )
        source = self.project_root / manifest.moves[0].from_relative_path
        target = self.project_root / manifest.moves[0].to_relative_path
        work_unit = self.project_root / manifest.patched_files[0].relative_path
        drifted = work_unit.read_bytes() + b"\n<!-- unrelated edit -->\n"
        work_unit.write_bytes(drifted)

        result = rollback_local_manifest(
            manifest,
            manifest_path=self.manifest_path,
        )

        self.assertFalse(result.is_valid)
        self.assertIn("rollback-wwu-drift", {issue.code for issue in result.issues})
        self.assertFalse(source.exists())
        self.assertTrue(target.is_file())
        self.assertEqual(drifted, work_unit.read_bytes())

    def test_interrupted_local_rollback_can_be_retried(self) -> None:
        selected = ("CH04_S102_WT_001.wav", "CH04_D001_WT_001.wav")
        manifest, _ = apply_selected_files_locally(
            build_plan(self.project_root, batch=True),
            only=selected,
            manifest_path=self.manifest_path,
        )
        call_count = 0

        def interrupted_move(path: Path, target: Path) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("simulated interruption")
            move_file_no_replace(path, target)

        with patch(
            "wwise_p4_source_relocator.rollback.move_file_no_replace",
            side_effect=interrupted_move,
        ):
            interrupted = rollback_local_manifest(
                manifest,
                manifest_path=self.manifest_path,
            )

        self.assertFalse(interrupted.is_valid)
        self.assertEqual("failed", read_rollback_manifest(self.manifest_path).status)

        retried = rollback_local_manifest(
            read_rollback_manifest(self.manifest_path),
            manifest_path=self.manifest_path,
        )

        self.assertTrue(retried.is_valid)
        self.assertTrue(
            all(
                (self.project_root / move.from_relative_path).is_file()
                for move in manifest.moves
            )
        )
        self.assertTrue(
            all(
                not (self.project_root / move.to_relative_path).exists()
                for move in manifest.moves
            )
        )

    def test_old_manifest_defaults_to_perforce_mode(self) -> None:
        document = {
            "schemaVersion": 1,
            "createdAt": "2026-07-16T00:00:00+00:00",
            "projectRoot": self.project_root.as_posix(),
            "changelist": None,
            "status": "prepared",
            "moves": [{"from": "a.wav", "to": "b.wav"}],
            "patchedFiles": [],
            "affectedObjects": [],
            "unmanagedFilesToDelete": [],
        }

        parsed = RollbackManifest.from_dict(json.loads(json.dumps(document)))

        self.assertEqual("perforce", parsed.operation_mode)
        self.assertIsNone(parsed.moves[0].source_sha256)

    def test_cli_dispatches_local_manifest_without_constructing_perforce(
        self,
    ) -> None:
        manifest, _ = apply_selected_files_locally(
            build_plan(self.project_root),
            only=("CH04_S102_WT_001.wav",),
            manifest_path=self.manifest_path,
        )
        with (
            patch(
                "wwise_p4_source_relocator.cli.P4Client",
                side_effect=AssertionError("Perforce must not be constructed"),
            ),
            patch(
                "wwise_p4_source_relocator.cli.validate_live_wwise_manifest_at_url",
                return_value=ValidationResult(()),
            ),
        ):
            validated = main(
                [
                    "validate-apply",
                    "--manifest",
                    str(self.manifest_path),
                    "--waapi-url",
                    "http://127.0.0.1:8090/waapi",
                ]
            )
            rolled_back = main(
                ["rollback", "--manifest", str(self.manifest_path)]
            )

        self.assertEqual(0, validated)
        self.assertEqual(0, rolled_back)
        self.assertEqual("local-filesystem", manifest.operation_mode)


if __name__ == "__main__":
    unittest.main()

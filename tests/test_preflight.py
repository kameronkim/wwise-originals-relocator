from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.models import RelocationPlan, RelocationPlanItem
from wwise_p4_source_relocator.p4_client import P4Connection
from wwise_p4_source_relocator.preflight import (
    P4WorkspaceProbe,
    validate_relocation_plan,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class FakeWorkspaceProbe:
    def __init__(
        self,
        *,
        available: bool = True,
        opened: bool = False,
        local_changes: bool = False,
    ) -> None:
        self.available = available
        self.opened = opened
        self.local_changes = local_changes
        self.workspace_checks: list[Path] = []
        self.opened_checks: list[Path] = []
        self.local_change_checks: list[Path] = []

    def is_available(self) -> bool:
        return self.available

    def is_in_workspace(self, path: Path) -> bool:
        self.workspace_checks.append(path)
        return True

    def is_opened(self, path: Path) -> bool:
        self.opened_checks.append(path)
        return self.opened

    def has_local_changes(self, path: Path) -> bool:
        self.local_change_checks.append(path)
        return self.local_changes


def move_item(**overrides) -> RelocationPlanItem:
    values = {
        "object_path": r"\Containers\Default Work Unit\VO\Temp_VO\Script\CH04\CH04_S102_WT_001",
        "guid": "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}",
        "source_file_name": "CH04_S102_WT_001.wav",
        "from_relative_path": "Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav",
        "to_relative_path": "Originals/Voices/English(US)/Script/CH04/CH04_S102_WT_001.wav",
        "work_unit_path": "Actor-Mixer Hierarchy/Default Work Unit.wwu",
        "action": "move-and-patch",
    }
    values.update(overrides)
    return RelocationPlanItem(**values)


def plan_with(item: RelocationPlanItem) -> RelocationPlan:
    return RelocationPlan(
        project_root=FIXTURE_ROOT,
        object_root=r"\Containers\Default Work Unit\VO\Temp_VO",
        chapter="CH04",
        items=(item,),
    )


class PreflightTests(unittest.TestCase):
    def test_workspace_probe_prefetches_path_states_in_three_commands(self) -> None:
        source = Path("/workspace/Originals/Source A.wav")
        target = Path("/workspace/Originals/Target A.wav")
        work_unit = Path("/workspace/Actor-Mixer Hierarchy/VO.wwu")
        depot_paths = {
            str(source): "//depot/Originals/Source A.wav",
            str(target): "//depot/Originals/Target A.wav",
            str(work_unit): "//depot/Actor-Mixer Hierarchy/VO.wwu",
        }

        def tagged_result(argv: tuple[str, ...], **_: object):
            if "where" in argv:
                paths = argv[argv.index("where") + 1 :]
                output = "".join(
                    f"... depotFile {depot_paths[path]}\n"
                    f"... clientFile //client/{Path(path).name}\n"
                    f"... path {path}\n"
                    for path in paths
                )
            elif "opened" in argv:
                output = (
                    f"... depotFile {depot_paths[str(source)]}\n"
                    "... action edit\n"
                )
            else:
                output = (
                    f"... depotFile {depot_paths[str(work_unit)]}\n"
                    "... type text\n"
                )
            return subprocess.CompletedProcess(
                argv, 0, stdout=output, stderr=""
            )

        probe = P4WorkspaceProbe(executable="p4.exe")
        with patch("subprocess.run", side_effect=tagged_result) as run:
            probe.prefetch(
                workspace_paths=(source, target, work_unit),
                opened_paths=(source, work_unit),
                local_change_paths=(work_unit,),
            )

            self.assertTrue(probe.is_in_workspace(target))
            self.assertTrue(probe.is_opened(source))
            self.assertFalse(probe.is_opened(work_unit))
            self.assertTrue(probe.has_local_changes(work_unit))

        self.assertEqual(3, run.call_count)
        self.assertEqual(3, probe.metrics()["commandCount"])
        self.assertTrue(
            all(
                "-ztag" in call.args[0]
                for call in run.call_args_list[:2]
            )
        )
        self.assertNotIn("-ztag", run.call_args_list[2].args[0])

    def test_workspace_prefetch_chunks_large_path_sets(self) -> None:
        paths = tuple(Path(f"/workspace/source-{index:03}.wav") for index in range(65))

        def where_result(argv: tuple[str, ...], **_: object):
            requested = argv[argv.index("where") + 1 :]
            output = "".join(
                f"... depotFile //depot/{Path(path).name}\n"
                f"... path {path}\n"
                for path in requested
            )
            return subprocess.CompletedProcess(
                argv, 0, stdout=output, stderr=""
            )

        probe = P4WorkspaceProbe(executable="p4.exe")
        with patch("subprocess.run", side_effect=where_result) as run:
            probe.prefetch(
                workspace_paths=paths,
                opened_paths=(),
                local_change_paths=(),
            )

        self.assertEqual(3, run.call_count)
        self.assertTrue(all(probe.is_in_workspace(path) for path in paths))

    def test_one_hundred_item_plan_uses_twelve_read_only_p4_calls(self) -> None:
        items = tuple(
            move_item(
                object_path=f"{move_item().object_path}_{index:03}",
                guid=f"{{00000000-0000-0000-0000-{index:012}}}",
                source_file_name=f"line-{index:03}.wav",
                from_relative_path=(
                    "Originals/Voices/English(US)/Scenario/CH04/"
                    f"line-{index:03}.wav"
                ),
                to_relative_path=(
                    "Originals/Voices/English(US)/Script/CH04/"
                    f"line-{index:03}.wav"
                ),
            )
            for index in range(100)
        )
        plan = RelocationPlan(
            project_root=FIXTURE_ROOT,
            object_root=r"\Containers\Default Work Unit\VO\Temp_VO",
            chapter="CH04",
            items=items,
        )

        def batch_result(argv: tuple[str, ...], **_: object):
            if "where" not in argv:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="", stderr=""
                )
            requested = argv[argv.index("where") + 1 :]
            output = "".join(
                f"... depotFile //depot/{index:03}\n"
                f"... clientFile //client/{index:03}\n"
                f"... path {path}\n"
                for index, path in enumerate(requested)
            )
            return subprocess.CompletedProcess(
                argv, 0, stdout=output, stderr=""
            )

        probe = P4WorkspaceProbe(executable="p4.exe")
        with (
            patch("shutil.which", return_value="C:/Tools/p4.exe"),
            patch("subprocess.run", side_effect=batch_result) as run,
        ):
            validate_relocation_plan(plan, probe=probe)

        self.assertEqual(12, run.call_count)
        self.assertEqual(12, probe.metrics()["commandCount"])

    def test_workspace_probe_uses_selected_p4v_connection(self) -> None:
        project_path = Path("C:/Work/Pilot.wproj")
        probe = P4WorkspaceProbe(
            executable="p4.exe",
            connection=P4Connection(
                port="ssl:perforce.example.com:1666",
                user="audio.user",
                client="audio-workspace",
            ),
        )
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "mapped"

            self.assertTrue(probe.is_in_workspace(project_path))

        self.assertEqual(
            (
                "p4.exe",
                "-p",
                "ssl:perforce.example.com:1666",
                "-u",
                "audio.user",
                "-c",
                "audio-workspace",
                "where",
                str(project_path),
            ),
            run.call_args.args[0],
        )

    def test_valid_move_passes_with_clean_workspace(self) -> None:
        result = validate_relocation_plan(
            plan_with(move_item()), probe=FakeWorkspaceProbe()
        )

        self.assertTrue(result.is_valid)

    def test_workspace_probe_checks_for_unopened_local_changes(self) -> None:
        project_path = Path("C:/Work/Temp_VO.wwu")
        probe = P4WorkspaceProbe(executable="p4.exe")
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "//depot/Dev/Temp_VO.wwu\n"

            self.assertTrue(probe.has_local_changes(project_path))

        self.assertEqual(
            ("p4.exe", "diff", "-se", str(project_path)),
            run.call_args.args[0],
        )

    def test_existing_work_unit_local_changes_are_a_hard_error(self) -> None:
        result = validate_relocation_plan(
            plan_with(move_item()),
            probe=FakeWorkspaceProbe(local_changes=True),
        )

        self.assertIn(
            "work-unit-local-changes",
            {issue.code for issue in result.issues},
        )

    def test_repeated_paths_are_only_probed_once(self) -> None:
        item = move_item()
        plan = RelocationPlan(
            project_root=FIXTURE_ROOT,
            object_root=r"\Containers\Default Work Unit\VO\Temp_VO",
            chapter="CH04",
            items=(item, item),
        )
        probe = FakeWorkspaceProbe()

        validate_relocation_plan(plan, probe=probe)

        self.assertEqual(3, len(probe.workspace_checks))
        self.assertEqual(2, len(probe.opened_checks))
        self.assertEqual(1, len(probe.local_change_checks))

    def test_missing_p4_is_a_hard_error(self) -> None:
        result = validate_relocation_plan(
            plan_with(move_item()), probe=FakeWorkspaceProbe(available=False)
        )

        self.assertIn("p4-unavailable", {issue.code for issue in result.issues})

    def test_existing_target_is_a_hard_error(self) -> None:
        item = move_item(
            to_relative_path="Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav"
        )

        result = validate_relocation_plan(
            plan_with(item), probe=FakeWorkspaceProbe()
        )

        codes = {issue.code for issue in result.issues}
        self.assertIn("same-path", codes)
        self.assertIn("target-exists", codes)

    def test_manual_review_item_fails_validation(self) -> None:
        item = move_item(
            action="manual-review",
            to_relative_path=None,
            reason="Wwise object has multiple audio sources",
        )

        result = validate_relocation_plan(
            plan_with(item), probe=FakeWorkspaceProbe()
        )

        self.assertEqual("manual-review", result.issues[0].code)

    def test_path_outside_project_is_a_hard_error(self) -> None:
        item = move_item(from_relative_path="../outside.wav")

        result = validate_relocation_plan(
            plan_with(item), probe=FakeWorkspaceProbe()
        )

        self.assertIn("outside-project", {issue.code for issue in result.issues})


if __name__ == "__main__":
    unittest.main()

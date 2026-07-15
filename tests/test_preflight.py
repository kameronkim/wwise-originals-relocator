from pathlib import Path
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

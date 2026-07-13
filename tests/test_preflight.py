from pathlib import Path
import unittest

from wwise_p4_source_relocator.models import RelocationPlan, RelocationPlanItem
from wwise_p4_source_relocator.preflight import validate_relocation_plan


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class FakeWorkspaceProbe:
    def __init__(self, *, available: bool = True, opened: bool = False) -> None:
        self.available = available
        self.opened = opened

    def is_available(self) -> bool:
        return self.available

    def is_in_workspace(self, path: Path) -> bool:
        return True

    def is_opened(self, path: Path) -> bool:
        return self.opened


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
    def test_valid_move_passes_with_clean_workspace(self) -> None:
        result = validate_relocation_plan(
            plan_with(move_item()), probe=FakeWorkspaceProbe()
        )

        self.assertTrue(result.is_valid)

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

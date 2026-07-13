from pathlib import Path
import unittest

from wwise_p4_source_relocator.models import ScanResult, SourceItem
from wwise_p4_source_relocator.planner import build_noop_plan, build_relocation_plan
from wwise_p4_source_relocator.wwise_xml import parse_source_references


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
FIXTURE_WWU = FIXTURE_ROOT / "Actor-Mixer Hierarchy" / "Default Work Unit.wwu"


class NoOpPlannerTests(unittest.TestCase):
    def test_every_discovered_source_is_explicitly_skipped(self) -> None:
        references = parse_source_references(FIXTURE_WWU, project_root=FIXTURE_ROOT)

        plan = build_noop_plan(FIXTURE_ROOT, references)

        self.assertEqual("no-op", plan.to_dict()["mode"])
        self.assertEqual(2, len(plan.items))
        self.assertTrue(all(item.action == "skip" for item in plan.items))
        self.assertTrue(
            all("Source inspection only" in item.reason for item in plan.items)
        )


class RelocationPlannerTests(unittest.TestCase):
    def source_item(
        self,
        *,
        category: str | None = "Script",
        paths: tuple[str, ...] = (
            "Originals/Voices/English(US)/Scenario/CH04/line.wav",
        ),
        guid: str = "{8886C06E-4664-4CEA-B3F1-8668CCDF3683}",
    ) -> SourceItem:
        return SourceItem(
            object_path=rf"\Containers\Default Work Unit\VO\Temp_VO\{category}\CH04\line",
            guid=guid,
            category=category,
            source_relative_paths=paths,
            work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
            language="English(US)",
            chapter="CH04",
        )

    def plan(self, *items: SourceItem):
        scan = ScanResult(
            project_root=FIXTURE_ROOT,
            object_root=r"\Containers\Default Work Unit\VO\Temp_VO",
            chapter="CH04",
            items=tuple(items),
        )
        return build_relocation_plan(scan)

    def test_script_source_maps_to_script_originals_folder(self) -> None:
        item = self.plan(self.source_item()).items[0]

        self.assertEqual("move-and-patch", item.action)
        self.assertEqual(
            "Originals/Voices/English(US)/Script/CH04/line.wav",
            item.to_relative_path,
        )

    def test_already_correct_source_is_skipped(self) -> None:
        item = self.plan(
            self.source_item(
                category="Dialog",
                paths=("Originals/Voices/English(US)/Dialog/CH04/line.wav",),
            )
        ).items[0]

        self.assertEqual("skip", item.action)

    def test_cutscene_and_dynamic_sources_map_to_their_category_folders(self) -> None:
        for category in ("Cutscene", "Dynamic"):
            with self.subTest(category=category):
                item = self.plan(self.source_item(category=category)).items[0]
                self.assertEqual("move-and-patch", item.action)
                self.assertEqual(
                    f"Originals/Voices/English(US)/{category}/CH04/line.wav",
                    item.to_relative_path,
                )

    def test_unknown_category_requires_manual_review(self) -> None:
        item = self.plan(self.source_item(category="Ambient")).items[0]

        self.assertEqual("manual-review", item.action)
        self.assertIn("not supported", item.reason)

    def test_multiple_sources_require_manual_review(self) -> None:
        item = self.plan(
            self.source_item(
                paths=(
                    "Originals/Voices/English(US)/Scenario/CH04/one.wav",
                    "Originals/Voices/English(US)/Scenario/CH04/two.wav",
                )
            )
        ).items[0]

        self.assertEqual("manual-review", item.action)
        self.assertIn("multiple audio sources", item.reason)

    def test_shared_source_requires_manual_review_for_both_objects(self) -> None:
        first = self.source_item()
        second = self.source_item(
            guid="{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}"
        )

        items = self.plan(first, second).items

        self.assertTrue(all(item.action == "manual-review" for item in items))
        self.assertTrue(all("share" in (item.reason or "") for item in items))


if __name__ == "__main__":
    unittest.main()

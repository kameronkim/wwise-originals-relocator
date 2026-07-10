from pathlib import Path
import unittest

from wwise_p4_source_relocator.planner import build_noop_plan
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


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import tempfile
import unittest

from wwise_p4_source_relocator.cli import main


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
FIXTURE_WWU = FIXTURE_ROOT / "Actor-Mixer Hierarchy" / "Default Work Unit.wwu"


class ReportTests(unittest.TestCase):
    def test_inspector_writes_json_and_markdown_noop_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            json_path = output_root / "plan.json"
            markdown_path = output_root / "plan.md"

            exit_code = main(
                [
                    "inspect-wwu",
                    "--wwu",
                    str(FIXTURE_WWU),
                    "--project-root",
                    str(FIXTURE_ROOT),
                    "--json-out",
                    str(json_path),
                    "--markdown-out",
                    str(markdown_path),
                ]
            )

            self.assertEqual(0, exit_code)
            json_report = json_path.read_text(encoding="utf-8")
            self.assertIn('"mode": "no-op"', json_report)
            self.assertIn('"objectGuid":', json_report)
            self.assertIn('"sourceRelativePath":', json_report)
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertIn("Sources discovered: 2", markdown)
            self.assertIn("Mutations performed: 0", markdown)
            self.assertIn("CH04_S102_WT_001", markdown)


if __name__ == "__main__":
    unittest.main()

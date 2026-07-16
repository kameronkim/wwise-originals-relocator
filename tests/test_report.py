from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.cli import main
from wwise_p4_source_relocator.models import ScanResult, SourceItem, ValidationResult
from wwise_p4_source_relocator.report import render_validation, write_json_document


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
FIXTURE_WWU = FIXTURE_ROOT / "Actor-Mixer Hierarchy" / "Default Work Unit.wwu"


class ReportTests(unittest.TestCase):
    def test_failed_atomic_json_replace_preserves_existing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollback-manifest.json"
            write_json_document(ValidationResult(()), path)
            original = path.read_bytes()

            with patch(
                "wwise_p4_source_relocator.report._replace_file",
                side_effect=PermissionError("manifest is locked"),
            ):
                with self.assertRaises(PermissionError):
                    write_json_document(
                        ScanResult(
                            project_root=FIXTURE_ROOT,
                            object_root="root",
                            chapter="CH04",
                            items=(),
                        ),
                        path,
                    )

            self.assertEqual(original, path.read_bytes())
            self.assertEqual([], list(path.parent.glob(f".{path.name}.*.tmp")))

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

    def test_plan_command_writes_json_and_markdown_reports(self) -> None:
        scan = ScanResult(
            project_root=FIXTURE_ROOT,
            object_root=r"\Containers\Default Work Unit\VO\Temp_VO",
            chapter="CH04",
            items=(
                SourceItem(
                    object_path=r"\Containers\Default Work Unit\VO\Temp_VO\Script\CH04\CH04_S102_WT_001",
                    guid="{8886C06E-4664-4CEA-B3F1-8668CCDF3683}",
                    category="Script",
                    source_relative_paths=(
                        "Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav",
                    ),
                    work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
                    language="English(US)",
                    chapter="CH04",
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            scan_path = output_root / "scan.json"
            plan_path = output_root / "plan.json"
            write_json_document(scan, scan_path)

            exit_code = main(
                ["plan", "--scan", str(scan_path), "--out", str(plan_path)]
            )

            self.assertEqual(0, exit_code)
            self.assertIn(
                '"action": "move-and-patch"',
                plan_path.read_text(encoding="utf-8"),
            )
            markdown = plan_path.with_suffix(".md").read_text(encoding="utf-8")
            self.assertIn("Move candidates: 1", markdown)

    def test_validation_report_includes_perforce_opened_state_summary(self) -> None:
        report = render_validation(
            ValidationResult(
                (),
                details={
                    "perforce": {
                        "moveAddCount": 2,
                        "moveDeleteCount": 2,
                        "movePairCount": 2,
                        "expectedMoveCount": 2,
                        "workUnitEditCount": 1,
                        "expectedWorkUnitCount": 1,
                    }
                },
            )
        )

        self.assertIn("## Perforce Opened State", report)
        self.assertIn("WAV move/add: 2 / 2", report)
        self.assertIn("Linked move pairs: 2 / 2", report)


if __name__ == "__main__":
    unittest.main()

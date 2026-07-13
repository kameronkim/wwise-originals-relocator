from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.readiness import (
    _p4_contains_project,
    inspect_pilot_readiness,
    render_readiness_markdown,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class PilotReadinessTests(unittest.TestCase):
    def test_workspace_probe_checks_the_project_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            project_file = project_root / "Pilot.wproj"
            project_file.write_text("<WwiseDocument/>", encoding="utf-8")

            with patch("wwise_p4_source_relocator.readiness.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "mapped"

                self.assertTrue(_p4_contains_project(project_root))

            self.assertEqual(str(project_file), run.call_args.args[0][-1])

    def test_ready_project_passes_all_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "WwiseProject"
            shutil.copytree(FIXTURE_ROOT, project_root)
            (project_root / "WwiseProject.wproj").write_text(
                "<WwiseDocument/>", encoding="utf-8"
            )

            readiness = inspect_pilot_readiness(
                project_root,
                p4_available=True,
                p4_workspace=True,
                waapi_client_available=True,
                waapi_reachable=True,
            )

            self.assertTrue(readiness.ready)
            self.assertTrue(
                all(check.status == "pass" for check in readiness.checks)
            )
            markdown = render_readiness_markdown(readiness)
            self.assertIn("Ready: yes", markdown)
            self.assertIn("Found 2 WWU source reference(s)", markdown)

    def test_empty_project_reports_actionable_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)

            readiness = inspect_pilot_readiness(
                project_root,
                p4_available=False,
                p4_workspace=False,
                waapi_client_available=False,
                waapi_reachable=False,
            )

            self.assertFalse(readiness.ready)
            failures = {
                check.name for check in readiness.checks if check.status == "fail"
            }
            self.assertEqual(
                {
                    "wwise-project",
                    "originals-wav",
                    "wwu-sources",
                    "p4-cli",
                    "p4-workspace",
                    "waapi-client",
                    "waapi-server",
                },
                failures,
            )


if __name__ == "__main__":
    unittest.main()

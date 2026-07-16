from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from wwise_p4_source_relocator.cli import build_parser


class CliExitTests(unittest.TestCase):
    def test_bootstrap_defaults_to_three_type_temp_vo_fixture(self) -> None:
        args = build_parser().parse_args(
            ("bootstrap-project", "--project-root", "WwiseRelocatorPilot")
        )

        self.assertEqual(
            r"\Containers\Default Work Unit\VO\Temp_VO",
            args.object_root,
        )
        self.assertIsNone(args.category)
        self.assertIsNone(args.sound_name)

    def test_python_module_propagates_command_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                (
                    sys.executable,
                    "-m",
                    "wwise_p4_source_relocator",
                    "doctor",
                    "--project-root",
                    str(Path(temporary)),
                ),
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ready: no", result.stdout)

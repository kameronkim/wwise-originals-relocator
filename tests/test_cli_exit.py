from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CliExitTests(unittest.TestCase):
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

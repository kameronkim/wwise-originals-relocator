from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

from wwise_p4_source_relocator.gui.app import _run_portable_smoke_check


class GuiAppTests(unittest.TestCase):
    def test_smoke_check_rejects_missing_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index_path = Path(directory) / "index.html"
            report_path = Path(directory) / "smoke.txt"

            with (
                patch.dict(
                    "os.environ",
                    {"WWISE_RELOCATOR_SMOKE_REPORT": str(report_path)},
                ),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(1, _run_portable_smoke_check(index_path))

            self.assertIn("Portable smoke check failed", report_path.read_text())


if __name__ == "__main__":
    unittest.main()

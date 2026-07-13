from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from wwise_p4_source_relocator.gui.app import _run_portable_smoke_check


class GuiAppTests(unittest.TestCase):
    def test_smoke_check_rejects_missing_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index_path = Path(directory) / "index.html"

            with redirect_stderr(StringIO()):
                self.assertEqual(1, _run_portable_smoke_check(index_path))


if __name__ == "__main__":
    unittest.main()

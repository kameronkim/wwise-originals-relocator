from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from wwise_p4_source_relocator import __version__


REPO_ROOT = Path(__file__).parents[1]


class PortablePackageTests(unittest.TestCase):
    def test_preparation_adds_operator_files_and_removes_legacy_guide(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_root = Path(directory) / "WwiseOriginalsRelocator"
            app_root.mkdir()
            (app_root / "WwiseOriginalsRelocator.exe").write_bytes(b"")
            (app_root / "사용가이드.md").write_text("old guide", encoding="utf-8")

            subprocess.run(
                (
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "prepare_portable.py"),
                    "--app-root",
                    str(app_root),
                ),
                check=True,
                cwd=REPO_ROOT,
            )

            guide = (app_root / "사용가이드.html").read_text(encoding="utf-8")
            self.assertIn(f"버전 {__version__}", guide)
            self.assertNotIn("{{APP_VERSION}}", guide)
            self.assertNotIn("http://", guide)
            self.assertNotIn("https://", guide)
            self.assertTrue((app_root / "LICENSE.txt").is_file())
            self.assertEqual(
                f"Wwise Originals Relocator {__version__}\n",
                (app_root / "VERSION.txt").read_text(encoding="utf-8"),
            )
            self.assertFalse((app_root / "사용가이드.md").exists())


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from wwise_p4_source_relocator import __version__


REPO_ROOT = Path(__file__).parents[1]


class PortablePackageTests(unittest.TestCase):
    def test_release_metadata_uses_package_version(self) -> None:
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())

        self.assertEqual(["version"], config["project"]["dynamic"])
        self.assertEqual(
            {"attr": "wwise_p4_source_relocator.__version__"},
            config["tool"]["setuptools"]["dynamic"]["version"],
        )
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        release_version = __version__.replace("rc", "-rc.")
        self.assertIn(release_version, changelog)
        self.assertIn("real multi-file Wwise and Perforce", changelog)

    def test_portable_workflow_covers_integration_branches_and_source(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "portable.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('      - "feature/**"', workflow)
        self.assertIn("      - develop", workflow)
        self.assertIn("      - main", workflow)
        self.assertIn('      - "src/**"', workflow)

    def test_canonical_usage_guide_is_gui_focused(self) -> None:
        guide = (REPO_ROOT / "docs" / "usage-guide.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Portable 사용 가이드", guide)
        self.assertIn("빠른 시작", guide)
        self.assertIn("선택 파일 적용과 Rollback", guide)
        self.assertIn("다중 파일 안전 적용", guide)
        self.assertIn("프로그램은 submit하지 않습니다", guide)
        self.assertIn("현재 버전의 안전 범위", guide)
        self.assertNotIn("apply --only", guide)
        self.assertFalse((REPO_ROOT / "docs" / "portable-gui.html").exists())

    def test_private_development_documents_are_not_tracked(self) -> None:
        private_documents = (
            "RELEASING.md",
            "docs/cli-operations-guide.html",
            "docs/development-spec.md",
            "docs/live-wwise-pilot.md",
            "docs/local-perforce-pilot.md",
            "docs/portable-gui.md",
        )

        for relative_path in private_documents:
            with self.subTest(path=relative_path):
                self.assertFalse((REPO_ROOT / relative_path).exists())

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

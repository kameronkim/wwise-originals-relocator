from pathlib import Path
import unittest


ASSET_ROOT = (
    Path(__file__).parents[1]
    / "src"
    / "wwise_p4_source_relocator"
    / "gui"
    / "assets"
)


class GuiAssetTests(unittest.TestCase):
    def test_desktop_assets_are_packaged_together(self) -> None:
        index = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn('href="styles.css"', index)
        self.assertIn('src="app.js"', index)
        self.assertTrue((ASSET_ROOT / "styles.css").is_file())
        self.assertTrue((ASSET_ROOT / "app.js").is_file())

    def test_gui_does_not_expose_apply_or_rollback_commands(self) -> None:
        index = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        script = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('id="run-apply"', index)
        self.assertNotIn('id="run-rollback"', index)
        self.assertNotIn("invoke('apply'", script)
        self.assertNotIn("invoke('rollback'", script)
        self.assertIn("읽기 전용", index)
        self.assertIn("확인 내용", index)
        self.assertIn("validation-issues", index)
        self.assertIn("app-version", index)


if __name__ == "__main__":
    unittest.main()

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

        self.assertIn('href="styles.css?v=6"', index)
        self.assertIn('src="app.js?v=6"', index)
        self.assertTrue((ASSET_ROOT / "styles.css").is_file())
        self.assertTrue((ASSET_ROOT / "app.js").is_file())

    def test_gui_exposes_guarded_single_file_apply_and_rollback(self) -> None:
        index = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        script = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        styles = (ASSET_ROOT / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="run-apply"', index)
        self.assertIn('id="run-rollback"', index)
        self.assertIn('id="run-validate-apply"', index)
        self.assertIn("'run_apply'", script)
        self.assertIn("'run_rollback'", script)
        self.assertIn("'run_validate_apply'", script)
        self.assertIn("한 번에 한 파일", index)
        self.assertIn("이 프로그램은 submit을 실행하지 않습니다", index)
        self.assertIn("확인 내용", index)
        self.assertIn("validation-issues", index)
        self.assertIn("app-version", index)
        self.assertIn('id="offline-test-mode"', index)
        self.assertIn("Perforce 없이 로컬 테스트", index)
        self.assertIn("offlineTestMode", script)
        self.assertNotIn("Apply · Rollback 기능 없음", index)
        self.assertIn("환경 확인과 이동 계획까지는 프로젝트를 변경하지 않습니다", index)
        self.assertNotIn("설치나 시스템 설정 변경 없이", index)
        self.assertIn("--blue: #83b7d4", styles)
        self.assertNotIn("--green:", styles)
        self.assertIn("font-weight: 600", styles)
        self.assertIn(".app-shell { height: 100%;", styles)
        self.assertIn("body { height: 100%;", styles)


if __name__ == "__main__":
    unittest.main()

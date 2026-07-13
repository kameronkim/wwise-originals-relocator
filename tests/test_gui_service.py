from pathlib import Path
import tempfile
import unittest

from wwise_p4_source_relocator.gui.bridge import GuiApi
from wwise_p4_source_relocator.gui.service import (
    DEFAULT_SETTINGS,
    GuiServiceError,
    LocalTestWorkspaceProbe,
    PortableSettingsStore,
    ReadOnlyGuiService,
)
from wwise_p4_source_relocator.models import (
    ScanResult,
    SourceItem,
    ValidationResult,
)
from wwise_p4_source_relocator.readiness import PilotReadiness, ReadinessCheck
from wwise_p4_source_relocator.waapi_reader import WaapiError


def ready(project_root: str | Path, **_: object) -> PilotReadiness:
    return PilotReadiness(
        Path(project_root),
        (ReadinessCheck("project-root", "pass", "Project is ready"),),
    )


def not_ready(project_root: str | Path, **_: object) -> PilotReadiness:
    return PilotReadiness(
        Path(project_root),
        (ReadinessCheck("p4-workspace", "fail", "Project is not mapped"),),
    )


def scan(**values: object) -> ScanResult:
    return ScanResult(
        project_root=Path(str(values["project_root"])),
        object_root=str(values["object_root"]),
        chapter=str(values["chapter"]),
        items=(
            SourceItem(
                object_path=r"\Containers\Default Work Unit\VO\Script\CH04\line",
                guid="{8886C06E-4664-4CEA-B3F1-8668CCDF3683}",
                category="Script",
                source_relative_paths=(
                    "Originals/Voices/English(US)/Scenario/CH04/line.wav",
                ),
                work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
                language="English(US)",
                chapter="CH04",
            ),
        ),
    )


def validate(*_: object, **__: object) -> ValidationResult:
    return ValidationResult(())


def failing_scan(**_: object) -> ScanResult:
    raise WaapiError("scan timed out")


class PortableSettingsStoreTests(unittest.TestCase):
    def test_settings_are_saved_beside_portable_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PortableSettingsStore(directory)

            self.assertEqual(DEFAULT_SETTINGS, store.load())
            saved = store.save(
                {
                    **DEFAULT_SETTINGS,
                    "projectRoot": "  /tmp/WwiseProject  ",
                    "offlineTestMode": True,
                }
            )

            self.assertEqual("/tmp/WwiseProject", saved["projectRoot"])
            self.assertTrue(saved["offlineTestMode"])
            self.assertTrue(store.settings_path.is_file())
            self.assertEqual(saved, store.load())


class ReadOnlyGuiServiceTests(unittest.TestCase):
    def make_service(self, data_root: Path, **overrides: object) -> ReadOnlyGuiService:
        arguments = {
            "data_root": data_root,
            "readiness_inspector": ready,
            "scanner": scan,
            "plan_validator": validate,
        }
        arguments.update(overrides)
        return ReadOnlyGuiService(**arguments)

    def settings(self, project_root: Path) -> dict[str, object]:
        return {
            **DEFAULT_SETTINGS,
            "projectRoot": str(project_root),
            "p4Executable": "/tools/p4",
        }

    def test_initial_state_exposes_read_only_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory) / "data")

            state = service.initial_state()

            self.assertEqual(
                {
                    "readOnly": True,
                    "apply": False,
                    "rollback": False,
                    "installsDependencies": False,
                    "offlineTestMode": True,
                },
                state["capabilities"],
            )
            self.assertEqual("0.1.0", state["system"]["appVersion"])

    def test_doctor_writes_portable_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root / "data")

            result = service.run_doctor(self.settings(root / "project"))

            self.assertTrue(result["ready"])
            reports = result["reports"]
            self.assertTrue(Path(reports["json"]).is_file())
            self.assertTrue(Path(reports["markdown"]).is_file())
            self.assertTrue(
                Path(reports["json"]).is_relative_to((root / "data").resolve())
            )

    def test_plan_writes_reports_without_applying_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(root / "data")

            result = service.run_plan(self.settings(project_root))

            self.assertEqual(1, result["counts"]["move-and-patch"])
            self.assertEqual("move-and-patch", result["items"][0]["action"])
            self.assertTrue(result["validation"]["valid"])
            self.assertTrue(
                all(Path(path).is_file() for path in result["reports"].values())
            )

    def test_plan_is_blocked_when_doctor_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(
                root / "data", readiness_inspector=not_ready
            )

            with self.assertRaisesRegex(GuiServiceError, "Project is not mapped"):
                service.run_plan(self.settings(root / "project"))

    def test_plan_reports_an_actionable_waapi_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(root / "data", scanner=failing_scan)

            with self.assertRaisesRegex(
                GuiServiceError, "Wwise WAAPI에서 source를 읽지 못했습니다"
            ):
                service.run_plan(self.settings(project_root))

    def test_offline_mode_skips_only_perforce_readiness_checks(self) -> None:
        calls: list[dict[str, object]] = []

        def inspect(project_root: str | Path, **values: object) -> PilotReadiness:
            calls.append(values)
            return ready(project_root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root / "data", readiness_inspector=inspect)
            settings = {
                **self.settings(root / "project"),
                "p4Executable": "",
                "offlineTestMode": True,
            }

            result = service.run_doctor(settings)

            self.assertTrue(result["offlineTestMode"])
            self.assertIs(calls[0]["p4_available"], True)
            self.assertIs(calls[0]["p4_workspace"], True)

    def test_offline_report_marks_perforce_checks_as_skipped(self) -> None:
        def inspected(project_root: str | Path, **_: object) -> PilotReadiness:
            return PilotReadiness(
                Path(project_root),
                (
                    ReadinessCheck("project-root", "pass", "Project is ready"),
                    ReadinessCheck("p4-cli", "pass", "p4 CLI is available"),
                    ReadinessCheck("p4-workspace", "pass", "Project is mapped"),
                ),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(
                root / "data", readiness_inspector=inspected
            )

            result = service.run_doctor(
                {
                    **self.settings(root / "project"),
                    "offlineTestMode": True,
                }
            )

            perforce_checks = result["checks"][1:]
            self.assertTrue(
                all(
                    "Skipped in local test mode" in check["message"]
                    for check in perforce_checks
                )
            )
            report = Path(result["reports"]["markdown"]).read_text(encoding="utf-8")
            self.assertIn("no Perforce command was executed", report)

    def test_offline_plan_uses_local_validation_probe(self) -> None:
        probes: list[object] = []

        def capture_validation(*_: object, **values: object) -> ValidationResult:
            probes.append(values["probe"])
            return ValidationResult(())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data", plan_validator=capture_validation
            )
            settings = {
                **self.settings(project_root),
                "p4Executable": "",
                "offlineTestMode": True,
            }

            result = service.run_plan(settings)

            self.assertTrue(result["offlineTestMode"])
            self.assertIsInstance(probes[0], LocalTestWorkspaceProbe)
            self.assertTrue(probes[0].is_available())
            self.assertFalse(probes[0].is_opened(project_root))

    def test_bridge_returns_actionable_errors_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = GuiApi(
                self.make_service(root / "data", readiness_inspector=not_ready)
            )

            result = api.run_plan(self.settings(root / "project"))

            self.assertFalse(result["ok"])
            self.assertIn("Project is not mapped", result["error"])


if __name__ == "__main__":
    unittest.main()

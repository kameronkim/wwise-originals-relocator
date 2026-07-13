from pathlib import Path
import tempfile
import unittest

from wwise_p4_source_relocator.applier import ApplyError
from wwise_p4_source_relocator.gui.bridge import GuiApi
from wwise_p4_source_relocator.gui.service import (
    DEFAULT_SETTINGS,
    GuiServiceError,
    LocalTestWorkspaceProbe,
    PortableGuiService,
    PortableSettingsStore,
)
from wwise_p4_source_relocator.models import (
    AffectedObjectRecord,
    MoveRecord,
    PatchedFileRecord,
    RollbackManifest,
    ScanResult,
    SourceItem,
    ValidationResult,
)
from wwise_p4_source_relocator.readiness import PilotReadiness, ReadinessCheck
from wwise_p4_source_relocator.report import write_json_document
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


def fake_apply(plan, **values: object):
    item = plan.items[0]
    manifest = RollbackManifest(
        created_at="2026-07-13T00:00:00+00:00",
        project_root=plan.project_root,
        changelist=values["changelist"],
        moves=(MoveRecord(item.from_relative_path, item.to_relative_path),),
        patched_files=(
            PatchedFileRecord(
                relative_path=item.work_unit_path,
                object_guid=item.guid,
                old_xml_path="old.wav",
                new_xml_path="new.wav",
                original_sha256="a" * 64,
                patched_sha256="b" * 64,
            ),
        ),
        affected_objects=(
            AffectedObjectRecord(
                object_path=item.object_path,
                guid=item.guid,
                before_source_relative_path=item.from_relative_path,
                after_source_relative_path=item.to_relative_path,
            ),
        ),
        unmanaged_files_to_delete=(),
        status="applied",
    )
    write_json_document(manifest, values["manifest_path"])
    return manifest, ValidationResult(())


def fake_rollback(manifest, **values: object) -> ValidationResult:
    write_json_document(manifest.with_status("rolled-back"), values["manifest_path"])
    return ValidationResult(())


def failed_apply_with_recovery_manifest(plan, **values: object):
    manifest, _ = fake_apply(plan, **values)
    write_json_document(
        manifest.with_status("failed"), values["manifest_path"]
    )
    raise ApplyError("automatic rollback failed")


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


class PortableGuiServiceTests(unittest.TestCase):
    def make_service(self, data_root: Path, **overrides: object) -> PortableGuiService:
        arguments = {
            "data_root": data_root,
            "readiness_inspector": ready,
            "scanner": scan,
            "plan_validator": validate,
        }
        arguments.update(overrides)
        return PortableGuiService(**arguments)

    def settings(self, project_root: Path) -> dict[str, object]:
        return {
            **DEFAULT_SETTINGS,
            "projectRoot": str(project_root),
            "p4Executable": "/tools/p4",
        }

    def test_initial_state_exposes_single_file_mutation_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory) / "data")

            state = service.initial_state()

            self.assertEqual(
                {
                    "readOnly": False,
                    "apply": True,
                    "rollback": True,
                    "installsDependencies": False,
                    "offlineTestMode": True,
                },
                state["capabilities"],
            )
            self.assertEqual("0.1.0", state["system"]["appVersion"])

    def test_gui_applies_one_planned_file_and_recovers_it_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data",
                applier=fake_apply,
                rollbacker=fake_rollback,
                p4_client_factory=lambda _: object(),
                workspace_probe_factory=lambda _: object(),
            )
            settings = {
                **self.settings(project_root),
                "changelist": "123456",
            }
            service.run_plan(settings)

            applied = service.run_apply(settings, "line.wav", "line.wav")

            self.assertTrue(applied["applied"])
            self.assertEqual("123456", applied["activeOperation"]["changelist"])
            self.assertTrue(Path(applied["reports"]["manifest"]).is_file())
            recovered = service.initial_state()["activeOperation"]
            self.assertEqual("line.wav", recovered["sourceFileName"])

            rolled_back = service.run_rollback(settings, "line.wav")

            self.assertTrue(rolled_back["rolledBack"])
            self.assertIsNone(rolled_back["activeOperation"])
            self.assertIsNone(service.initial_state()["activeOperation"])

    def test_offline_mode_cannot_apply_a_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(root / "data", applier=fake_apply)
            settings = {
                **self.settings(project_root),
                "offlineTestMode": True,
            }
            service.run_plan(settings)

            with self.assertRaisesRegex(GuiServiceError, "로컬 테스트"):
                service.run_apply(settings, "line.wav", "line.wav")

    def test_apply_rejects_a_non_numeric_changelist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(root / "data", applier=fake_apply)
            settings = {
                **self.settings(project_root),
                "changelist": "release-audio",
            }
            service.run_plan(settings)

            with self.assertRaisesRegex(GuiServiceError, "숫자만"):
                service.run_apply(settings, "line.wav", "line.wav")

    def test_failed_automatic_recovery_remains_available_to_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data",
                applier=failed_apply_with_recovery_manifest,
                p4_client_factory=lambda _: object(),
                workspace_probe_factory=lambda _: object(),
            )
            settings = self.settings(project_root)
            service.run_plan(settings)

            result = service.run_apply(settings, "line.wav", "line.wav")

            self.assertFalse(result["applied"])
            self.assertEqual("failed", result["activeOperation"]["status"])
            self.assertIn("Rollback을 다시 실행", result["errorMessage"])

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

    def test_plan_uses_and_persists_an_automatically_detected_http_endpoint(
        self,
    ) -> None:
        scan_calls: list[dict[str, object]] = []

        def detected(project_root: str | Path, **_: object) -> PilotReadiness:
            return PilotReadiness(
                Path(project_root),
                (ReadinessCheck("waapi-server", "pass", "HTTP detected"),),
                waapi_url="http://127.0.0.1:8090/waapi",
                waapi_transport="http",
            )

        def capture_scan(**values: object) -> ScanResult:
            scan_calls.append(values)
            return scan(**values)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data",
                readiness_inspector=detected,
                scanner=capture_scan,
            )

            result = service.run_plan(self.settings(project_root))

            self.assertEqual(
                "http://127.0.0.1:8090/waapi", scan_calls[0]["url"]
            )
            self.assertEqual("http", result["waapiConnection"]["transport"])
            self.assertEqual(
                "http://127.0.0.1:8090/waapi",
                service.store.load()["waapiUrl"],
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

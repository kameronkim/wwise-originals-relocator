import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator import __version__
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
    ValidationIssue,
    ValidationResult,
)
from wwise_p4_source_relocator.p4_client import (
    P4CommandError,
    P4Connection,
    P4ConnectionInfo,
)
from wwise_p4_source_relocator.readiness import PilotReadiness, ReadinessCheck
from wwise_p4_source_relocator.report import (
    read_rollback_manifest,
    write_json_document,
)
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


def scan_two(**values: object) -> ScanResult:
    first = scan(**values).items[0]
    second = SourceItem(
        object_path=r"\Containers\Default Work Unit\VO\Script\CH04\line_two",
        guid="{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}",
        category="Script",
        source_relative_paths=(
            "Originals/Voices/English(US)/Scenario/CH04/line_two.wav",
        ),
        work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
        language="English(US)",
        chapter="CH04",
    )
    return ScanResult(
        project_root=Path(str(values["project_root"])),
        object_root=str(values["object_root"]),
        chapter=str(values["chapter"]),
        items=(first, second),
    )


def validate(*_: object, **__: object) -> ValidationResult:
    return ValidationResult(())


def failing_scan(**_: object) -> ScanResult:
    raise WaapiError("scan timed out")


def fake_apply(plan, **values: object):
    selected_names = values.get("only")
    if isinstance(selected_names, str):
        selected_names = (selected_names,)
    selected = [
        item for item in plan.items if item.source_file_name in selected_names
    ]
    manifest = RollbackManifest(
        created_at="2026-07-13T00:00:00+00:00",
        project_root=plan.project_root,
        changelist=values["changelist"],
        moves=tuple(
            MoveRecord(item.from_relative_path, item.to_relative_path)
            for item in selected
        ),
        patched_files=tuple(
            PatchedFileRecord(
                relative_path=item.work_unit_path,
                object_guid=item.guid,
                old_xml_path="old.wav",
                new_xml_path="new.wav",
                original_sha256="a" * 64,
                patched_sha256="b" * 64,
            )
            for item in selected
        ),
        affected_objects=tuple(
            AffectedObjectRecord(
                object_path=item.object_path,
                guid=item.guid,
                before_source_relative_path=item.from_relative_path,
                after_source_relative_path=item.to_relative_path,
            )
            for item in selected
        ),
        unmanaged_files_to_delete=(),
        status="awaiting-wwise-reload",
    )
    write_json_document(manifest, values["manifest_path"])
    return manifest, ValidationResult(())


def fake_rollback(manifest, **values: object) -> ValidationResult:
    write_json_document(manifest.with_status("rolled-back"), values["manifest_path"])
    return ValidationResult(())


def failed_rollback(*_: object, **__: object) -> ValidationResult:
    raise OSError("p4 process stopped")


def failed_apply_with_recovery_manifest(plan, **values: object):
    manifest, _ = fake_apply(plan, **values)
    write_json_document(
        manifest.with_status("failed"), values["manifest_path"]
    )
    raise ApplyError("automatic rollback failed")


class FixedOpenedProbe:
    def __init__(self, opened: bool) -> None:
        self.opened = opened

    def is_opened(self, path: Path) -> bool:
        return self.opened


def write_handed_off_manifest(
    data_root: Path,
    project_root: Path,
    *,
    final_state: str,
) -> Path:
    source = project_root / "Originals/Scenario/line.wav"
    target = project_root / "Originals/Script/line.wav"
    work_unit = project_root / "Actor-Mixer Hierarchy/Default Work Unit.wwu"
    source.parent.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    work_unit.parent.mkdir(parents=True, exist_ok=True)
    original_wwu = b"original work unit"
    patched_wwu = b"patched work unit"
    if final_state == "applied":
        target.write_bytes(b"wave")
        work_unit.write_bytes(patched_wwu)
    else:
        source.write_bytes(b"wave")
        work_unit.write_bytes(original_wwu)
    manifest = RollbackManifest(
        created_at="2026-07-13T00:00:00+00:00",
        project_root=project_root,
        changelist="123456",
        moves=(
            MoveRecord(
                "Originals/Scenario/line.wav",
                "Originals/Script/line.wav",
            ),
        ),
        patched_files=(
            PatchedFileRecord(
                relative_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
                object_guid="{8886C06E-4664-4CEA-B3F1-8668CCDF3683}",
                old_xml_path="old.wav",
                new_xml_path="new.wav",
                original_sha256=hashlib.sha256(original_wwu).hexdigest(),
                patched_sha256=hashlib.sha256(patched_wwu).hexdigest(),
            ),
        ),
        affected_objects=(
            AffectedObjectRecord(
                object_path=r"\Containers\Default Work Unit\VO\Script\CH04\line",
                guid="{8886C06E-4664-4CEA-B3F1-8668CCDF3683}",
                before_source_relative_path="Originals/Scenario/line.wav",
                after_source_relative_path="Originals/Script/line.wav",
            ),
        ),
        unmanaged_files_to_delete=(),
        status="handed-off",
    )
    manifest_path = data_root / "reports/20260713T000000.000000Z-apply/rollback-manifest.json"
    write_json_document(manifest, manifest_path)
    return manifest_path


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
            self.assertEqual(__version__, state["system"]["appVersion"])
            self.assertEqual([], state["operationHistory"]["entries"])

    def test_initial_state_imports_connection_exported_by_p4v(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory) / "data")
            service.store.save(
                {
                    **DEFAULT_SETTINGS,
                    "p4Port": "old-server:1666",
                    "p4User": "old.user",
                    "p4Client": "old-workspace",
                }
            )
            with patch.dict(
                "os.environ",
                {
                    "P4PORT": "ssl:perforce.example.com:1666",
                    "P4USER": "audio.user",
                    "P4CLIENT": "audio-workspace",
                    "P4CHARSET": "utf8",
                },
            ):
                state = service.initial_state()

            self.assertEqual(
                "ssl:perforce.example.com:1666",
                state["settings"]["p4Port"],
            )
            self.assertEqual("audio.user", state["settings"]["p4User"])
            self.assertEqual("audio-workspace", state["settings"]["p4Client"])
            self.assertEqual("p4v-environment", state["system"]["p4ConnectionSource"])

    def test_detect_p4_connection_persists_non_secret_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(root / "data")
            info = P4ConnectionInfo(
                P4Connection(
                    port="ssl:perforce.example.com:1666",
                    user="audio.user",
                    client="audio-workspace",
                    charset="utf8",
                ),
                server_version="P4D/NTX64/2026.1",
            )
            with patch(
                "wwise_p4_source_relocator.gui.service.query_p4_connection",
                return_value=info,
            ) as query:
                result = service.detect_p4_connection(self.settings(project_root))

            self.assertEqual("audio-workspace", result["settings"]["p4Client"])
            self.assertEqual("audio.user", service.store.load()["p4User"])
            self.assertTrue(result["workspaceConfigured"])
            self.assertNotIn("password", service.store.settings_path.read_text())
            self.assertEqual(project_root.resolve(), query.call_args.kwargs["cwd"])

    def test_detect_p4_connection_reports_when_workspace_is_still_needed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(root / "data")
            info = P4ConnectionInfo(
                P4Connection(
                    port="perforce.example.com:1666",
                    user="audio.user",
                ),
                client_candidates=("workspace-a", "workspace-b"),
            )
            with patch(
                "wwise_p4_source_relocator.gui.service.query_p4_connection",
                return_value=info,
            ):
                result = service.detect_p4_connection(self.settings(project_root))

            self.assertFalse(result["workspaceConfigured"])
            self.assertEqual(
                ["workspace-a", "workspace-b"],
                result["workspaceCandidates"],
            )
            self.assertEqual("", result["settings"]["p4Client"])

    def test_detect_p4_connection_recovers_from_a_stale_saved_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(root / "data")
            values = self.settings(project_root)
            values["p4Port"] = "localhost.localdomain:1666"
            detected = P4ConnectionInfo(
                P4Connection(
                    port="172.16.32.101:1666",
                    user="developer",
                    client="audio-workspace",
                )
            )
            failure = P4CommandError(
                1,
                ("p4", "info"),
                output="",
                stderr="Connect to server failed",
            )
            with (
                patch.dict("os.environ", {}, clear=True),
                patch(
                    "wwise_p4_source_relocator.gui.service.query_p4_connection",
                    side_effect=(failure, detected),
                ) as query,
            ):
                result = service.detect_p4_connection(values)

            self.assertEqual(
                "172.16.32.101:1666",
                result["settings"]["p4Port"],
            )
            self.assertEqual(
                "localhost.localdomain:1666",
                query.call_args_list[0].kwargs["connection"].port,
            )
            self.assertIsNone(query.call_args_list[1].kwargs["connection"].port)

    def test_operation_history_is_sorted_and_filtered_to_selected_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            project_root = root / "project"
            other_project = root / "other-project"
            project_root.mkdir()
            other_project.mkdir()

            first_path = write_handed_off_manifest(
                data_root,
                project_root,
                final_state="applied",
            )
            first = read_rollback_manifest(first_path)
            first_document = first.to_dict()
            first_document["createdAt"] = "2026-07-13T00:00:00+00:00"
            first_document["status"] = "rolled-back"
            write_json_document(RollbackManifest.from_dict(first_document), first_path)

            second_document = first.to_dict()
            second_document["createdAt"] = "2026-07-14T00:00:00+00:00"
            second_document["status"] = "completed"
            second_path = (
                data_root
                / "reports/20260714T000000.000000Z-apply/rollback-manifest.json"
            )
            write_json_document(RollbackManifest.from_dict(second_document), second_path)
            second_path.with_name("apply-verification.json").write_text(
                json.dumps({
                    "manifest": second_path.resolve().as_posix(),
                    "validationReport": "/reports/validation.md",
                }),
                encoding="utf-8",
            )

            other_document = first.to_dict()
            other_document["projectRoot"] = other_project.as_posix()
            other_path = (
                data_root
                / "reports/20260715T000000.000000Z-apply/rollback-manifest.json"
            )
            write_json_document(RollbackManifest.from_dict(other_document), other_path)
            corrupt_path = (
                data_root
                / "reports/20260716T000000.000000Z-apply/rollback-manifest.json"
            )
            corrupt_path.parent.mkdir(parents=True)
            corrupt_path.write_text("not json", encoding="utf-8")

            service = self.make_service(data_root)
            history = service.get_operation_history(self.settings(project_root))

            self.assertEqual(2, history["totalCount"])
            self.assertEqual(1, history["unreadableCount"])
            self.assertEqual(
                ["completed", "rolled-back"],
                [entry["status"] for entry in history["entries"]],
            )
            self.assertTrue(history["entries"][0]["validationRecorded"])
            self.assertEqual(
                "/reports/validation.md",
                history["entries"][0]["validationReport"],
            )
            self.assertEqual(
                (data_root / "reports").resolve().as_posix(),
                history["reportRoot"],
            )

    def test_gui_applies_one_planned_file_and_recovers_it_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data",
                applier=fake_apply,
                rollbacker=fake_rollback,
                p4_client_factory=lambda *_: object(),
                workspace_probe_factory=lambda *_: object(),
            )
            settings = {
                **self.settings(project_root),
                "changelist": "123456",
            }
            service.run_plan(settings)

            applied = service.run_apply(settings, "line.wav", "line.wav")

            self.assertTrue(applied["applied"])
            self.assertEqual(
                "awaiting-wwise-reload",
                applied["activeOperation"]["status"],
            )
            self.assertEqual("123456", applied["activeOperation"]["changelist"])
            self.assertTrue(Path(applied["reports"]["manifest"]).is_file())
            recovered = service.initial_state()["activeOperation"]
            self.assertEqual("line.wav", recovered["sourceFileName"])

            rolled_back = service.run_rollback(settings, "line.wav")

            self.assertTrue(rolled_back["rolledBack"])
            self.assertIsNone(rolled_back["activeOperation"])
            self.assertIsNone(service.initial_state()["activeOperation"])

    def test_gui_applies_multiple_selected_files_as_one_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data",
                scanner=scan_two,
                applier=fake_apply,
                rollbacker=fake_rollback,
                p4_client_factory=lambda *_: object(),
                workspace_probe_factory=lambda *_: object(),
            )
            settings = self.settings(project_root)
            service.run_plan(settings)
            selected = ("line.wav", "line_two.wav")

            applied = service.run_apply(
                settings,
                list(selected),
                "\n".join(selected),
            )

            operation = applied["activeOperation"]
            self.assertEqual(2, operation["fileCount"])
            self.assertEqual(list(selected), operation["sourceFileNames"])
            self.assertEqual(2, len(operation["moves"]))

            rolled_back = service.run_rollback(
                settings,
                operation["confirmationToken"],
            )

            self.assertTrue(rolled_back["rolledBack"])
            self.assertIsNone(service.initial_state()["activeOperation"])

    def test_gui_validates_applied_file_against_perforce_and_live_wwise(self) -> None:
        calls: list[tuple[str, object]] = []

        def validate_applied(manifest, **values: object) -> ValidationResult:
            calls.append(("local", values["p4"]))
            return ValidationResult(())

        def validate_live(manifest, **values: object) -> ValidationResult:
            calls.append(("live", values["url"]))
            return ValidationResult(())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            p4 = object()
            service = self.make_service(
                root / "data",
                applier=fake_apply,
                applied_validator=validate_applied,
                live_validator=validate_live,
                p4_client_factory=lambda *_: p4,
                workspace_probe_factory=lambda *_: object(),
            )
            settings = {
                **self.settings(project_root),
                "waapiUrl": "http://127.0.0.1:8090/waapi",
            }
            service.run_plan(settings)
            service.run_apply(settings, "line.wav", "line.wav")

            result = service.run_validate_apply(settings)

            self.assertTrue(result["valid"])
            self.assertEqual("applied", result["activeOperation"]["status"])
            self.assertTrue(result["activeOperation"]["validated"])
            self.assertEqual(
                [("local", p4), ("live", "http://127.0.0.1:8090/waapi")],
                calls,
            )
            self.assertTrue(Path(result["reports"]["validation"]).is_file())
            self.assertTrue(Path(result["reports"]["verification"]).is_file())
            self.assertTrue(service.initial_state()["activeOperation"]["validated"])

            handed_off = service.run_handoff_apply(
                settings,
                "line.wav",
            )

            self.assertTrue(handed_off["handedOff"])
            self.assertEqual("handed-off", handed_off["activeOperation"]["status"])
            self.assertEqual(
                "handed-off",
                service.initial_state()["activeOperation"]["status"],
            )

    def test_gui_keeps_handoff_locked_while_perforce_paths_are_opened(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            data_root = root / "data"
            write_handed_off_manifest(data_root, project_root, final_state="applied")
            service = self.make_service(
                data_root,
                workspace_probe_factory=lambda *_: FixedOpenedProbe(True),
            )
            settings = self.settings(project_root)

            result = service.run_check_handoff(settings)

            self.assertFalse(result["completed"])
            self.assertEqual(3, result["pendingPathCount"])
            self.assertEqual("handed-off", result["activeOperation"]["status"])

    def test_gui_completes_handoff_after_perforce_and_wwise_are_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            data_root = root / "data"
            manifest_path = write_handed_off_manifest(
                data_root,
                project_root,
                final_state="applied",
            )
            service = self.make_service(
                data_root,
                live_validator=lambda *_, **__: ValidationResult(()),
                workspace_probe_factory=lambda *_: FixedOpenedProbe(False),
            )

            result = service.run_check_handoff(self.settings(project_root))

            self.assertTrue(result["completed"])
            self.assertEqual("completed", result["finalState"])
            self.assertEqual(
                "completed", read_rollback_manifest(manifest_path).status
            )
            self.assertIsNone(service.initial_state()["activeOperation"])

    def test_gui_recognizes_an_external_perforce_revert_after_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            data_root = root / "data"
            manifest_path = write_handed_off_manifest(
                data_root,
                project_root,
                final_state="rolled-back",
            )
            service = self.make_service(
                data_root,
                workspace_probe_factory=lambda *_: FixedOpenedProbe(False),
            )

            result = service.run_check_handoff(self.settings(project_root))

            self.assertTrue(result["completed"])
            self.assertEqual("rolled-back", result["finalState"])
            self.assertTrue(result["requiresWwiseReload"])
            self.assertEqual(
                "rolled-back", read_rollback_manifest(manifest_path).status
            )

    def test_gui_combines_post_apply_validation_issues(self) -> None:
        local_issue = ValidationIssue("target-missing", "Target missing")
        live_issue = ValidationIssue("wwise-source-mismatch", "Wwise stale")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data",
                applier=fake_apply,
                applied_validator=lambda *_, **__: ValidationResult((local_issue,)),
                live_validator=lambda *_, **__: ValidationResult((live_issue,)),
                p4_client_factory=lambda *_: object(),
                workspace_probe_factory=lambda *_: object(),
            )
            settings = self.settings(project_root)
            service.run_plan(settings)
            service.run_apply(settings, "line.wav", "line.wav")

            result = service.run_validate_apply(settings)

            self.assertFalse(result["valid"])
            self.assertEqual(
                ["target-missing", "wwise-source-mismatch"],
                [issue["code"] for issue in result["validation"]["issues"]],
            )

    def test_gui_rejects_live_validation_for_failed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data",
                applier=failed_apply_with_recovery_manifest,
                p4_client_factory=lambda *_: object(),
                workspace_probe_factory=lambda *_: object(),
            )
            settings = self.settings(project_root)
            service.run_plan(settings)
            service.run_apply(settings, "line.wav", "line.wav")

            with self.assertRaisesRegex(GuiServiceError, "먼저 Rollback"):
                service.run_validate_apply(settings)

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
                p4_client_factory=lambda *_: object(),
                workspace_probe_factory=lambda *_: object(),
            )
            settings = self.settings(project_root)
            service.run_plan(settings)

            result = service.run_apply(settings, "line.wav", "line.wav")

            self.assertFalse(result["applied"])
            self.assertEqual("failed", result["activeOperation"]["status"])
            self.assertIn("Rollback을 다시 실행", result["errorMessage"])
            self.assertTrue(Path(result["reports"]["failure"]).is_file())
            self.assertIn(
                "automatic rollback failed",
                Path(result["reports"]["failure"]).read_text(encoding="utf-8"),
            )

    def test_rollback_exception_still_writes_a_validation_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data",
                applier=fake_apply,
                rollbacker=failed_rollback,
                p4_client_factory=lambda *_: object(),
                workspace_probe_factory=lambda *_: object(),
            )
            settings = self.settings(project_root)
            service.run_plan(settings)
            service.run_apply(settings, "line.wav", "line.wav")

            result = service.run_rollback(settings, "line.wav")

            self.assertFalse(result["rolledBack"])
            self.assertEqual(
                "rollback-exception",
                result["validation"]["issues"][0]["code"],
            )
            self.assertTrue(Path(result["reports"]["validation"]).is_file())
            self.assertEqual("failed", result["activeOperation"]["status"])

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

    def test_plan_persists_an_automatically_discovered_object_root(self) -> None:
        detected_root = r"\Containers\Default Work Unit\VO\Temp_VO"

        def detected_scan(**values: object) -> ScanResult:
            return ScanResult(
                project_root=Path(str(values["project_root"])),
                object_root=detected_root,
                chapter=str(values["chapter"]),
                items=scan(**values).items,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            service = self.make_service(
                root / "data",
                scanner=detected_scan,
            )
            settings = self.settings(project_root)
            settings["objectRoot"] = r"\Containers\Default Work Unit\VO"

            result = service.run_plan(settings)

            self.assertEqual(detected_root, result["objectRoot"])
            self.assertEqual(detected_root, service.store.load()["objectRoot"])

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

    def test_bridge_keeps_native_window_and_service_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = GuiApi(self.make_service(Path(directory) / "data"))
            api.bind_window(object())

            self.assertTrue(all(name.startswith("_") for name in vars(api)))
            self.assertEqual(
                [],
                [
                    name
                    for name in dir(api)
                    if not name.startswith("_")
                    and not callable(getattr(api, name))
                ],
            )


if __name__ == "__main__":
    unittest.main()

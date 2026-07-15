from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from time import perf_counter

from .. import __version__
from ..applier import ApplyError, apply_selected_files
from ..models import (
    RelocationPlan,
    RollbackManifest,
    ScanResult,
    ValidationIssue,
    ValidationResult,
)
from ..p4_client import (
    P4CommandError,
    P4Client,
    P4Connection,
    query_p4_connection,
)
from ..pilot_project import find_wwise_console
from ..planner import build_relocation_plan
from ..preflight import P4WorkspaceProbe, validate_relocation_plan
from ..project_paths import UnsafeProjectPath, resolve_project_path
from ..readiness import (
    PilotReadiness,
    ReadinessCheck,
    inspect_pilot_readiness,
    render_readiness_markdown,
)
from ..report import (
    read_rollback_manifest,
    render_relocation_plan,
    render_validation,
    write_json_document,
)
from ..rollback import rollback_manifest
from ..validator import (
    DEFAULT_LIVE_WWISE_BATCH_SIZE,
    validate_applied_manifest,
    validate_live_wwise_manifest_at_url,
)
from ..waapi_reader import WaapiError, scan_live


LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())


DEFAULT_SETTINGS: dict[str, object] = {
    "projectRoot": "",
    "objectRoot": r"\Containers\Default Work Unit\VO",
    "chapter": "CH04",
    "waapiUrl": "ws://127.0.0.1:8080/waapi",
    "p4Executable": "",
    "p4Port": "",
    "p4User": "",
    "p4Client": "",
    "p4Charset": "",
    "changelist": "",
    "offlineTestMode": False,
}


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


class GuiServiceError(RuntimeError):
    """An actionable operator error that is safe to show in the GUI."""


class LocalTestWorkspaceProbe:
    """Skip Perforce-only checks while keeping local plan validation active."""

    def is_available(self) -> bool:
        return True

    def is_in_workspace(self, path: Path) -> bool:
        return True

    def is_opened(self, path: Path) -> bool:
        return False

    def has_local_changes(self, path: Path) -> bool:
        return False


class PortableSettingsStore:
    def __init__(self, data_root: str | Path | None = None) -> None:
        self.data_root = resolve_data_root(data_root)
        self.settings_path = self.data_root / "settings.json"

    def load(self) -> dict[str, object]:
        settings = dict(DEFAULT_SETTINGS)
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return settings
        except (OSError, json.JSONDecodeError):
            return settings
        if isinstance(raw, dict):
            settings.update(_normalize_settings(raw))
        return settings

    def save(self, values: Mapping[str, object]) -> dict[str, object]:
        settings = dict(DEFAULT_SETTINGS)
        settings.update(_normalize_settings(values))
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return settings


class PortableGuiService:
    def __init__(
        self,
        *,
        data_root: str | Path | None = None,
        readiness_inspector: Callable[..., PilotReadiness] = inspect_pilot_readiness,
        scanner: Callable[..., ScanResult] = scan_live,
        planner: Callable[[ScanResult], RelocationPlan] = build_relocation_plan,
        plan_validator: Callable[..., ValidationResult] = validate_relocation_plan,
        applier: Callable[
            ..., tuple[RollbackManifest, ValidationResult]
        ] = apply_selected_files,
        applied_validator: Callable[
            ..., ValidationResult
        ] = validate_applied_manifest,
        live_validator: Callable[
            ..., ValidationResult
        ] = validate_live_wwise_manifest_at_url,
        rollbacker: Callable[..., ValidationResult] = rollback_manifest,
        p4_client_factory: Callable[[str, P4Connection], P4Client] | None = None,
        workspace_probe_factory: (
            Callable[[str, P4Connection], P4WorkspaceProbe] | None
        ) = None,
    ) -> None:
        self.store = PortableSettingsStore(data_root)
        self._readiness_inspector = readiness_inspector
        self._scanner = scanner
        self._planner = planner
        self._plan_validator = plan_validator
        self._applier = applier
        self._applied_validator = applied_validator
        self._live_validator = live_validator
        self._rollbacker = rollbacker
        self._p4_client_factory = p4_client_factory or _live_p4_client
        self._workspace_probe_factory = (
            workspace_probe_factory or P4WorkspaceProbe
        )
        self._planned_plan: RelocationPlan | None = None
        self._planned_validation: ValidationResult | None = None
        self._planned_settings: tuple[str, ...] | None = None

    def initial_state(self) -> dict[str, object]:
        settings = _with_environment_connection(self.store.load())
        detected_p4 = discover_p4_executable()
        if not settings["p4Executable"] and detected_p4:
            settings["p4Executable"] = detected_p4
        console = find_wwise_console()
        active_operations = self._active_manifests_for_settings(settings)
        operation_history = self._operation_history_for_settings(settings)
        return {
            "settings": settings,
            "system": {
                "appVersion": __version__,
                "platform": platform.system() or sys.platform,
                "portable": bool(getattr(sys, "frozen", False)),
                "dataRoot": self.store.data_root.as_posix(),
                "p4Executable": str(settings["p4Executable"]),
                "p4Detected": bool(settings["p4Executable"]),
                "p4Connection": _p4_connection(settings).to_dict(),
                "p4ConnectionSource": (
                    "p4v-environment"
                    if P4Connection.from_environment().configured
                    else "settings"
                ),
                "wwiseConsole": console.as_posix() if console else "",
                "wwiseDetected": console is not None,
            },
            "capabilities": {
                "readOnly": False,
                "apply": True,
                "rollback": True,
                "installsDependencies": False,
                "offlineTestMode": True,
            },
            "activeOperation": (
                _manifest_summary(*active_operations[0])
                if len(active_operations) == 1
                else None
            ),
            "activeOperationCount": len(active_operations),
            "operationHistory": operation_history,
        }

    def detect_p4_connection(
        self, values: Mapping[str, object]
    ) -> dict[str, object]:
        settings = dict(DEFAULT_SETTINGS)
        settings.update(_normalize_settings(values))
        executable = _p4_executable(settings)
        project_root = _optional_project_root(settings)
        connection = _p4_connection(settings)
        try:
            info = query_p4_connection(
                executable=executable,
                connection=connection,
                cwd=project_root,
            )
        except (OSError, subprocess.SubprocessError, P4CommandError) as exc:
            fallback = P4Connection.from_environment()
            if fallback != connection:
                LOGGER.warning(
                    "Configured Perforce context failed; retrying current p4 settings: %s",
                    exc,
                )
                try:
                    info = query_p4_connection(
                        executable=executable,
                        connection=fallback,
                        cwd=project_root,
                    )
                except (
                    OSError,
                    subprocess.SubprocessError,
                    P4CommandError,
                ) as fallback_exc:
                    raise GuiServiceError(
                        "P4V/Perforce 연결 정보를 확인하지 못했습니다. P4V에서 "
                        "올바른 서버와 workspace로 로그인했는지 확인하세요."
                    ) from fallback_exc
            else:
                raise GuiServiceError(
                    "P4V/Perforce 연결 정보를 확인하지 못했습니다. P4V에서 올바른 "
                    "서버와 workspace로 로그인했는지 확인하세요."
                ) from exc
        resolved = info.connection
        settings.update(
            {
                "p4Executable": executable,
                "p4Port": resolved.port or "",
                "p4User": resolved.user or "",
                "p4Client": resolved.client or "",
                "p4Charset": resolved.charset or "",
            }
        )
        saved = self.store.save(settings)
        return {
            "settings": saved,
            "connection": info.to_dict(),
            "workspaceConfigured": bool(resolved.client),
            "workspaceCandidates": list(info.client_candidates),
            "source": (
                "p4v-environment"
                if P4Connection.from_environment().configured
                else "perforce-settings"
            ),
        }

    def get_operation_history(
        self, values: Mapping[str, object]
    ) -> dict[str, object]:
        settings = self.store.load()
        settings.update(_normalize_settings(values))
        return self._operation_history_for_settings(settings)

    def update_settings(self, values: Mapping[str, object]) -> dict[str, object]:
        self._clear_planned_state()
        return self.store.save(values)

    def run_doctor(self, values: Mapping[str, object]) -> dict[str, object]:
        self._clear_planned_state()
        settings = self.store.save(values)
        project_root = _project_root(settings)
        p4_executable = _p4_executable(settings)
        p4_connection = _p4_connection(settings)
        configured_waapi_url = _required_setting(settings, "waapiUrl")
        waapi_host, waapi_port, waapi_path, waapi_secure = _waapi_endpoint(settings)
        offline_test_mode = _offline_test_mode(settings)
        readiness = self._readiness_inspector(
            project_root,
            p4_executable=p4_executable,
            p4_connection=p4_connection,
            p4_available=True if offline_test_mode else None,
            p4_connection_available=True if offline_test_mode else None,
            p4_workspace=True if offline_test_mode else None,
            waapi_host=waapi_host,
            waapi_port=waapi_port,
            waapi_path=waapi_path,
            waapi_secure=waapi_secure,
            waapi_url=configured_waapi_url,
        )
        if offline_test_mode:
            readiness = _mark_perforce_skipped(readiness)
        _save_detected_connections(self.store, settings, readiness)
        report_root = self._new_report_root("doctor")
        json_path = report_root / "readiness.json"
        markdown_path = report_root / "readiness.md"
        write_json_document(readiness, json_path)
        markdown_path.write_text(
            render_readiness_markdown(readiness), encoding="utf-8"
        )
        return {
            **readiness.to_dict(),
            "offlineTestMode": offline_test_mode,
            "reports": {
                "json": json_path.as_posix(),
                "markdown": markdown_path.as_posix(),
            },
        }

    def run_plan(self, values: Mapping[str, object]) -> dict[str, object]:
        operation_started = perf_counter()
        self._clear_planned_state()
        settings = self.store.save(values)
        project_root = _project_root(settings)
        p4_executable = _p4_executable(settings)
        p4_connection = _p4_connection(settings)
        configured_waapi_url = _required_setting(settings, "waapiUrl")
        waapi_host, waapi_port, waapi_path, waapi_secure = _waapi_endpoint(settings)
        offline_test_mode = _offline_test_mode(settings)
        readiness_started = perf_counter()
        readiness = self._readiness_inspector(
            project_root,
            p4_executable=p4_executable,
            p4_connection=p4_connection,
            p4_available=True if offline_test_mode else None,
            p4_connection_available=True if offline_test_mode else None,
            p4_workspace=True if offline_test_mode else None,
            waapi_host=waapi_host,
            waapi_port=waapi_port,
            waapi_path=waapi_path,
            waapi_secure=waapi_secure,
            waapi_url=configured_waapi_url,
        )
        readiness_ms = _elapsed_ms(readiness_started)
        if offline_test_mode:
            readiness = _mark_perforce_skipped(readiness)
        if not readiness.ready:
            failed = "; ".join(
                check.message for check in readiness.checks if check.status == "fail"
            )
            raise GuiServiceError(
                "환경 확인을 먼저 완료해 주세요. " + (failed or "준비 상태가 유효하지 않습니다.")
            )

        object_root = _required_setting(settings, "objectRoot")
        chapter = _required_setting(settings, "chapter")
        waapi_url = readiness.waapi_url or configured_waapi_url
        _save_detected_connections(self.store, settings, readiness)
        scan_started = perf_counter()
        try:
            scan = self._scanner(
                project_root=project_root,
                object_root=object_root,
                chapter=chapter,
                url=waapi_url,
            )
        except WaapiError as exc:
            raise GuiServiceError(
                "Wwise WAAPI에서 source를 읽지 못했습니다. Wwise에서 프로젝트가 "
                f"열려 있고 WAAPI가 활성화되어 있는지 확인하세요. 세부 정보: {exc}"
            ) from exc
        scan_ms = _elapsed_ms(scan_started)
        if scan.object_root != object_root:
            object_root = scan.object_root
            settings = {**settings, "objectRoot": object_root}
            self.store.save(settings)
        plan_started = perf_counter()
        plan = self._planner(scan)
        plan_ms = _elapsed_ms(plan_started)
        probe = (
            LocalTestWorkspaceProbe()
            if offline_test_mode
            else self._workspace_probe_factory(p4_executable, p4_connection)
        )
        validation_started = perf_counter()
        validation = self._plan_validator(plan, probe=probe)
        validation_ms = _elapsed_ms(validation_started)
        self._planned_plan = plan
        self._planned_validation = validation
        self._planned_settings = _plan_settings_signature(settings)

        report_root = self._new_report_root("plan")
        scan_path = report_root / "scan.json"
        plan_path = report_root / "plan.json"
        plan_markdown_path = report_root / "plan.md"
        validation_path = report_root / "validation.md"
        performance_path = report_root / "performance.json"
        report_started = perf_counter()
        write_json_document(scan, scan_path)
        write_json_document(plan, plan_path)
        plan_markdown_path.write_text(
            render_relocation_plan(plan), encoding="utf-8"
        )
        validation_path.write_text(
            render_validation(validation), encoding="utf-8"
        )
        report_ms = _elapsed_ms(report_started)
        counts = {
            action: sum(item.action == action for item in plan.items)
            for action in ("move-and-patch", "skip", "manual-review")
        }
        probe_metrics = getattr(probe, "metrics", None)
        perforce_metrics = (
            probe_metrics()
            if callable(probe_metrics)
            else {"commandCount": 0, "elapsedMs": 0.0, "batchSize": 0}
        )
        performance = {
            "schemaVersion": 1,
            "operation": "plan",
            "itemCount": len(plan.items),
            "workUnitCount": len(
                {item.work_unit_path.casefold() for item in plan.items}
            ),
            "durationsMs": {
                "readiness": readiness_ms,
                "waapiScan": scan_ms,
                "planBuild": plan_ms,
                "preflight": validation_ms,
                "reportWrite": report_ms,
                "total": _elapsed_ms(operation_started),
            },
            "perforce": perforce_metrics,
        }
        performance_path.write_text(
            json.dumps(performance, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return {
            "projectRoot": project_root.as_posix(),
            "objectRoot": object_root,
            "chapter": chapter,
            "offlineTestMode": offline_test_mode,
            "waapiConnection": (
                {
                    "url": readiness.waapi_url,
                    "transport": readiness.waapi_transport,
                }
                if readiness.waapi_url and readiness.waapi_transport
                else None
            ),
            "counts": counts,
            "items": [item.to_dict() for item in plan.items],
            "validation": validation.to_dict(),
            "performance": performance,
            "reports": {
                "scan": scan_path.as_posix(),
                "planJson": plan_path.as_posix(),
                "planMarkdown": plan_markdown_path.as_posix(),
                "validation": validation_path.as_posix(),
                "performance": performance_path.as_posix(),
            },
        }

    def run_apply(
        self,
        values: Mapping[str, object],
        source_file_names: object,
        confirmation: str,
    ) -> dict[str, object]:
        settings = self.store.save(values)
        project_root = _project_root(settings)
        if _offline_test_mode(settings):
            raise GuiServiceError(
                "Perforce 없는 로컬 테스트에서는 파일을 적용할 수 없습니다."
            )
        selected = _selected_file_names(source_file_names)
        if not selected or confirmation != _confirmation_token(selected):
            raise GuiServiceError("선택한 파일 목록 확인이 일치하지 않습니다.")
        if self._planned_plan is None or self._planned_validation is None:
            raise GuiServiceError(
                "이동 계획을 다시 만든 뒤 파일을 적용해 주세요."
            )
        if self._planned_settings != _plan_settings_signature(settings):
            raise GuiServiceError(
                "프로젝트 설정이 변경되었습니다. 환경 확인과 이동 계획을 "
                "다시 실행해 주세요."
            )
        if not self._planned_validation.is_valid:
            raise GuiServiceError(
                "사전 검증에 해결할 항목이 있습니다. 적용 전에 모두 "
                "확인해 주세요."
            )
        active = self._active_manifests(project_root)
        if active:
            raise GuiServiceError(
                "아직 복구되지 않은 파일 작업이 있습니다. 먼저 "
                "Rollback을 완료해 주세요."
            )

        p4_executable = _p4_executable(settings)
        p4_connection = _p4_connection(settings)
        report_root = self._new_report_root("apply")
        manifest_path = report_root / "rollback-manifest.json"
        validation_path = report_root / "apply-validation.md"
        changelist = _changelist_setting(settings)
        try:
            manifest, validation = self._applier(
                self._planned_plan,
                only=selected,
                changelist=changelist,
                manifest_path=manifest_path,
                p4=self._p4_client_factory(p4_executable, p4_connection),
                probe=self._workspace_probe_factory(
                    p4_executable,
                    p4_connection,
                ),
            )
        except ApplyError as exc:
            failure_path = report_root / "apply-failure.md"
            failure_path.write_text(
                "# Apply Failure\n\n"
                f"- Error: {exc}\n"
                f"- Manifest: {manifest_path.as_posix()}\n",
                encoding="utf-8",
            )
            recovery = self._active_manifests(project_root)
            if len(recovery) == 1:
                recovery_path, recovery_manifest = recovery[0]
                self._clear_planned_state()
                return {
                    "applied": False,
                    "activeOperation": _manifest_summary(
                        recovery_path, recovery_manifest
                    ),
                    "errorMessage": (
                        "파일 적용과 자동 복구를 완료하지 못했습니다. "
                        f"Rollback을 다시 실행해 주세요. 세부 정보: {exc}"
                    ),
                    "reports": {
                        "manifest": recovery_path.as_posix(),
                        "failure": failure_path.as_posix(),
                    },
                }
            raise GuiServiceError(
                "파일 적용을 완료하지 못했습니다. 자동 복구 결과를 "
                f"확인하세요. 세부 정보: {exc}. 실패 보고서: {failure_path}"
            ) from exc
        validation_path.write_text(
            render_validation(validation), encoding="utf-8"
        )
        self._clear_planned_state()
        return {
            "applied": True,
            "activeOperation": _manifest_summary(manifest_path, manifest),
            "validation": validation.to_dict(),
            "requiresWwiseReload": True,
            "reports": {
                "manifest": manifest_path.as_posix(),
                "validation": validation_path.as_posix(),
            },
        }

    def run_validate_apply(
        self,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        operation_started = perf_counter()
        settings = self.store.save(values)
        project_root = _project_root(settings)
        if _offline_test_mode(settings):
            raise GuiServiceError(
                "Perforce 없는 로컬 테스트에서는 적용 결과를 검증할 수 없습니다."
            )
        active = self._active_manifests(project_root)
        if len(active) != 1:
            raise GuiServiceError(
                "검증 가능한 작업 manifest를 정확히 하나 찾을 수 없습니다."
            )
        manifest_path, manifest = active[0]
        if manifest.status not in {"awaiting-wwise-reload", "applied"}:
            raise GuiServiceError(
                "Wwise 반영을 확인할 수 없는 manifest입니다. 먼저 Rollback을 "
                "실행해 주세요."
            )

        p4 = self._p4_client_factory(
            _p4_executable(settings),
            _p4_connection(settings),
        )
        local_started = perf_counter()
        local = self._applied_validator(manifest, p4=p4)
        local_ms = _elapsed_ms(local_started)
        live_started = perf_counter()
        try:
            live = self._live_validator(
                manifest,
                url=_required_setting(settings, "waapiUrl"),
            )
        except RuntimeError as exc:
            raise GuiServiceError(
                "Wwise 적용 상태를 읽지 못했습니다. Wwise에서 External Project "
                "Changes를 다시 불러오고 열린 설정창을 닫은 뒤 다시 확인해 주세요. "
                f"세부 정보: {exc}"
            ) from exc
        live_ms = _elapsed_ms(live_started)

        details = dict(local.details or {})
        if live.details:
            details["liveWwise"] = live.details
        result = ValidationResult(
            local.issues + live.issues,
            details=details or None,
        )
        report_root = self._new_report_root("validate-apply")
        validation_path = report_root / "apply-validation.md"
        performance_path = report_root / "performance.json"
        validation_path.write_text(render_validation(result), encoding="utf-8")
        verification_path = _verification_path(manifest_path)
        verified_manifest = manifest
        if result.is_valid and manifest.status == "awaiting-wwise-reload":
            verified_manifest = manifest.with_status("applied")
            write_json_document(verified_manifest, manifest_path)
        if result.is_valid:
            _write_apply_verification(
                verification_path,
                manifest_path=manifest_path,
                validation_path=validation_path,
            )
        else:
            verification_path.unlink(missing_ok=True)
        object_count = len(manifest.affected_objects)
        request_count = (
            object_count + DEFAULT_LIVE_WWISE_BATCH_SIZE - 1
        ) // DEFAULT_LIVE_WWISE_BATCH_SIZE
        performance = {
            "schemaVersion": 1,
            "operation": "validate-apply",
            "itemCount": object_count,
            "durationsMs": {
                "localValidation": local_ms,
                "liveWwiseValidation": live_ms,
                "total": _elapsed_ms(operation_started),
            },
            "liveWwise": {
                "objectCount": object_count,
                "batchSize": DEFAULT_LIVE_WWISE_BATCH_SIZE,
                "requestCount": request_count,
            },
        }
        performance_path.write_text(
            json.dumps(performance, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return {
            "valid": result.is_valid,
            "validation": result.to_dict(),
            "performance": performance,
            "activeOperation": {
                **_manifest_summary(manifest_path, verified_manifest),
                "validated": result.is_valid,
            },
            "reports": {
                "validation": validation_path.as_posix(),
                "performance": performance_path.as_posix(),
                "verification": (
                    verification_path.as_posix() if result.is_valid else None
                ),
            },
        }

    def run_handoff_apply(
        self,
        values: Mapping[str, object],
        confirmation: str,
    ) -> dict[str, object]:
        settings = self.store.save(values)
        project_root = _project_root(settings)
        if _offline_test_mode(settings):
            raise GuiServiceError(
                "Perforce 없는 로컬 테스트에서는 P4V 인계를 실행할 수 없습니다."
            )
        active = self._active_manifests(project_root)
        if len(active) != 1:
            raise GuiServiceError(
                "P4V로 인계할 작업 manifest를 정확히 하나 찾을 수 없습니다."
            )
        manifest_path, manifest = active[0]
        if manifest.status != "applied":
            raise GuiServiceError("현재 작업은 P4V 인계 단계가 아닙니다.")
        if confirmation != _manifest_confirmation_token(manifest):
            raise GuiServiceError("P4V 인계 파일 목록 확인이 일치하지 않습니다.")

        validation = self.run_validate_apply(settings)
        if not validation["valid"]:
            return {"handedOff": False, **validation}

        handed_off = manifest.with_status("handed-off")
        write_json_document(handed_off, manifest_path)
        validation_report = validation["reports"].get("validation")
        if isinstance(validation_report, str):
            _write_apply_verification(
                _verification_path(manifest_path),
                manifest_path=manifest_path,
                validation_path=Path(validation_report),
            )
        self._clear_planned_state()
        return {
            "handedOff": True,
            "activeOperation": _manifest_summary(manifest_path, handed_off),
            "reports": validation["reports"],
        }

    def run_check_handoff(
        self,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        settings = self.store.save(values)
        project_root = _project_root(settings)
        if _offline_test_mode(settings):
            raise GuiServiceError(
                "Perforce 없는 로컬 테스트에서는 P4V 마감을 확인할 수 없습니다."
            )
        active = self._active_manifests(project_root)
        if len(active) != 1:
            raise GuiServiceError(
                "마감 상태를 확인할 작업 manifest를 정확히 하나 찾을 수 없습니다."
            )
        manifest_path, manifest = active[0]
        if manifest.status != "handed-off":
            raise GuiServiceError("먼저 검증 완료 작업을 P4V로 인계해 주세요.")

        try:
            operation_paths = _manifest_operation_paths(manifest)
        except UnsafeProjectPath as exc:
            raise GuiServiceError(str(exc)) from exc
        probe = self._workspace_probe_factory(
            _p4_executable(settings),
            _p4_connection(settings),
        )
        try:
            opened_paths = [
                path
                for path in operation_paths
                if probe.is_opened(path)
            ]
        except OSError as exc:
            raise GuiServiceError(
                f"Perforce opened 상태를 읽지 못했습니다. 세부 정보: {exc}"
            ) from exc
        if opened_paths:
            return {
                "completed": False,
                "pendingPathCount": len(opened_paths),
                "activeOperation": _manifest_summary(manifest_path, manifest),
            }

        move_paths, work_units = _resolved_manifest_state(manifest)
        rolled_back_files = all(
            source.is_file() and not target.exists()
            for source, target in move_paths
        )
        rolled_back_work_units = all(
            path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
            for path, original_hash, _ in work_units
        )
        if rolled_back_files and rolled_back_work_units:
            rolled_back = manifest.with_status("rolled-back")
            write_json_document(rolled_back, manifest_path)
            self._clear_planned_state()
            return {
                "completed": True,
                "finalState": "rolled-back",
                "requiresWwiseReload": True,
                "activeOperation": None,
            }
        applied_files = all(
            not source.exists() and target.is_file()
            for source, target in move_paths
        )
        applied_work_units = all(
            path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == patched_hash
            for path, _, patched_hash in work_units
        )
        if not applied_files or not applied_work_units:
            raise GuiServiceError(
                "Perforce opened 상태는 정리되었지만 WAV 또는 Work Unit이 manifest와 "
                "일치하지 않습니다. P4V 상태와 보고서를 운영 담당자에게 전달해 주세요."
            )

        try:
            live = self._live_validator(
                manifest,
                url=_required_setting(settings, "waapiUrl"),
            )
        except RuntimeError as exc:
            raise GuiServiceError(
                "P4V 마감 뒤 Wwise 상태를 읽지 못했습니다. Wwise에서 External "
                "Project Changes를 다시 불러온 뒤 다시 확인해 주세요. "
                f"세부 정보: {exc}"
            ) from exc
        if not live.is_valid:
            report_root = self._new_report_root("complete-apply")
            validation_path = report_root / "completion-validation.md"
            validation_path.write_text(render_validation(live), encoding="utf-8")
            return {
                "completed": False,
                "validation": live.to_dict(),
                "activeOperation": _manifest_summary(manifest_path, manifest),
                "reports": {"validation": validation_path.as_posix()},
            }

        completed = manifest.with_status("completed")
        write_json_document(completed, manifest_path)
        self._clear_planned_state()
        return {
            "completed": True,
            "finalState": "completed",
            "activeOperation": None,
        }

    def run_rollback(
        self,
        values: Mapping[str, object],
        confirmation: str,
    ) -> dict[str, object]:
        settings = self.store.save(values)
        project_root = _project_root(settings)
        if _offline_test_mode(settings):
            raise GuiServiceError(
                "Perforce 없는 로컬 테스트에서는 Rollback을 실행할 수 없습니다."
            )
        active = self._active_manifests(project_root)
        if len(active) != 1:
            raise GuiServiceError(
                "Rollback 가능한 작업 manifest를 정확히 하나 "
                "찾을 수 없습니다."
            )
        manifest_path, manifest = active[0]
        if confirmation != _manifest_confirmation_token(manifest):
            raise GuiServiceError("Rollback 파일 목록 확인이 일치하지 않습니다.")
        if manifest.status == "handed-off":
            try:
                operation_paths = _manifest_operation_paths(manifest)
            except UnsafeProjectPath as exc:
                raise GuiServiceError(str(exc)) from exc
            probe = self._workspace_probe_factory(
                _p4_executable(settings),
                _p4_connection(settings),
            )
            try:
                all_opened = all(
                    probe.is_opened(path) for path in operation_paths
                )
            except OSError as exc:
                raise GuiServiceError(
                    f"Perforce opened 상태를 읽지 못했습니다. 세부 정보: {exc}"
                ) from exc
            if not all_opened:
                raise GuiServiceError(
                    "P4V에서 일부 또는 전체 파일이 이미 마감되었습니다. Rollback을 "
                    "실행하지 않고 P4V 마감 상태 확인을 먼저 눌러 주세요."
                )

        report_root = self._new_report_root("rollback")
        validation_path = report_root / "rollback-validation.md"
        try:
            result = self._rollbacker(
                manifest,
                p4=self._p4_client_factory(
                    _p4_executable(settings),
                    _p4_connection(settings),
                ),
                manifest_path=manifest_path,
            )
        except Exception as exc:
            LOGGER.exception("Rollback stopped unexpectedly")
            result = ValidationResult(
                (
                    ValidationIssue(
                        "rollback-exception",
                        f"Rollback stopped unexpectedly: {exc}",
                    ),
                )
            )
            write_json_document(manifest.with_status("failed"), manifest_path)
        validation_path.write_text(render_validation(result), encoding="utf-8")
        self._clear_planned_state()
        return {
            "rolledBack": result.is_valid,
            "validation": result.to_dict(),
            "requiresWwiseReload": result.is_valid,
            "activeOperation": None if result.is_valid else _manifest_summary(
                manifest_path, read_rollback_manifest(manifest_path)
            ),
            "reports": {
                "manifest": manifest_path.as_posix(),
                "validation": validation_path.as_posix(),
            },
        }

    def _active_manifests(
        self, project_root: Path
    ) -> list[tuple[Path, RollbackManifest]]:
        active: list[tuple[Path, RollbackManifest]] = []
        report_root = self.store.data_root / "reports"
        for path in sorted(report_root.glob("*-apply/rollback-manifest.json")):
            try:
                manifest = read_rollback_manifest(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if (
                manifest.status in {
                    "awaiting-wwise-reload",
                    "applied",
                    "handed-off",
                    "failed",
                }
                and len(manifest.moves) >= 1
                and len(manifest.moves) == len(manifest.patched_files)
                and len(manifest.moves) == len(manifest.affected_objects)
                and manifest.project_root.resolve() == project_root.resolve()
            ):
                active.append((path.resolve(), manifest))
        return active

    def _active_manifests_for_settings(
        self, settings: Mapping[str, object]
    ) -> list[tuple[Path, RollbackManifest]]:
        raw = settings.get("projectRoot")
        if not isinstance(raw, str) or not raw.strip():
            return []
        return self._active_manifests(Path(raw).expanduser().resolve())

    def _operation_history_for_settings(
        self, settings: Mapping[str, object], *, limit: int = 20
    ) -> dict[str, object]:
        report_root = self.store.data_root / "reports"
        raw_project_root = settings.get("projectRoot")
        if not isinstance(raw_project_root, str) or not raw_project_root.strip():
            return {
                "entries": [],
                "totalCount": 0,
                "unreadableCount": 0,
                "reportRoot": report_root.as_posix(),
            }

        project_root = Path(raw_project_root).expanduser().resolve()
        entries: list[dict[str, object]] = []
        unreadable_count = 0
        for manifest_path in report_root.glob("*-apply/rollback-manifest.json"):
            try:
                manifest = read_rollback_manifest(manifest_path)
            except (OSError, ValueError, json.JSONDecodeError):
                unreadable_count += 1
                continue
            if manifest.project_root.resolve() != project_root:
                continue
            if (
                not manifest.moves
                or len(manifest.moves) != len(manifest.patched_files)
                or len(manifest.moves) != len(manifest.affected_objects)
            ):
                unreadable_count += 1
                continue

            resolved_manifest_path = manifest_path.resolve()
            verification = _read_apply_verification(resolved_manifest_path)
            validation_report = (
                verification.get("validationReport")
                if verification is not None
                and isinstance(verification.get("validationReport"), str)
                else None
            )
            entries.append(
                {
                    **_manifest_summary(resolved_manifest_path, manifest),
                    "createdAt": manifest.created_at,
                    "validationRecorded": verification is not None,
                    "validationReport": validation_report,
                    "reportDirectory": resolved_manifest_path.parent.as_posix(),
                }
            )

        entries.sort(
            key=lambda entry: (str(entry["createdAt"]), str(entry["manifest"])),
            reverse=True,
        )
        return {
            "entries": entries[:limit],
            "totalCount": len(entries),
            "unreadableCount": unreadable_count,
            "reportRoot": report_root.as_posix(),
        }

    def _clear_planned_state(self) -> None:
        self._planned_plan = None
        self._planned_validation = None
        self._planned_settings = None

    def _new_report_root(self, operation: str) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        path = self.store.data_root / "reports" / f"{timestamp}-{operation}"
        path.mkdir(parents=True, exist_ok=False)
        return path


# Keep the original import available for callers built against the read-only GUI.
ReadOnlyGuiService = PortableGuiService


def resolve_data_root(value: str | Path | None = None) -> Path:
    if value is not None:
        return Path(value).expanduser().resolve()
    override = os.environ.get("WWISE_RELOCATOR_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return Path.cwd().resolve() / ".portable-data"


def discover_p4_executable() -> str | None:
    detected = shutil.which("p4")
    if detected:
        return str(Path(detected).resolve())
    candidates: list[Path] = []
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root) / "Perforce" / "p4.exe")
    elif sys.platform == "darwin":
        candidates.extend(
            (
                Path("/opt/homebrew/bin/p4"),
                Path("/usr/local/bin/p4"),
            )
        )
    return next((str(path.resolve()) for path in candidates if path.is_file()), None)


def _normalize_settings(values: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, default in DEFAULT_SETTINGS.items():
        value = values.get(key, default)
        if isinstance(default, str):
            normalized[key] = value.strip() if isinstance(value, str) else default
        elif isinstance(default, bool):
            normalized[key] = value if isinstance(value, bool) else default
    return normalized


def _project_root(settings: Mapping[str, object]) -> Path:
    raw = _required_setting(settings, "projectRoot")
    return Path(raw).expanduser().resolve()


def _optional_project_root(settings: Mapping[str, object]) -> Path | None:
    raw = _optional_setting(settings, "projectRoot")
    if raw is None:
        return None
    root = Path(raw).expanduser().resolve()
    return root if root.is_dir() else None


def _p4_executable(settings: Mapping[str, object]) -> str:
    configured = str(settings.get("p4Executable") or "").strip()
    return configured or discover_p4_executable() or "p4"


def _p4_connection(settings: Mapping[str, object]) -> P4Connection:
    environment = P4Connection.from_environment()
    return P4Connection(
        port=_optional_setting(settings, "p4Port") or environment.port,
        user=_optional_setting(settings, "p4User") or environment.user,
        client=_optional_setting(settings, "p4Client") or environment.client,
        charset=_optional_setting(settings, "p4Charset") or environment.charset,
    )


def _with_environment_connection(
    settings: Mapping[str, object],
) -> dict[str, object]:
    merged = dict(settings)
    environment = P4Connection.from_environment()
    for key, value in (
        ("p4Port", environment.port),
        ("p4User", environment.user),
        ("p4Client", environment.client),
        ("p4Charset", environment.charset),
    ):
        if value:
            merged[key] = value
    return merged


def _offline_test_mode(settings: Mapping[str, object]) -> bool:
    return settings.get("offlineTestMode") is True


def _optional_setting(settings: Mapping[str, object], key: str) -> str | None:
    value = settings.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _changelist_setting(settings: Mapping[str, object]) -> str | None:
    changelist = _optional_setting(settings, "changelist")
    if changelist is not None and not changelist.isdigit():
        raise GuiServiceError(
            "Perforce changelist 번호에는 숫자만 입력해 주세요."
        )
    return changelist


def _plan_settings_signature(settings: Mapping[str, object]) -> tuple[str, ...]:
    connection = _p4_connection(settings)
    return (
        str(_project_root(settings)),
        _required_setting(settings, "objectRoot"),
        _required_setting(settings, "chapter"),
        _p4_executable(settings),
        connection.port or "",
        connection.user or "",
        connection.client or "",
        connection.charset or "",
        "offline" if _offline_test_mode(settings) else "perforce",
    )


def _live_p4_client(
    executable: str,
    connection: P4Connection,
) -> P4Client:
    return P4Client(
        executable=executable,
        connection=connection,
        dry_run=False,
    )


def _manifest_summary(
    manifest_path: Path, manifest: RollbackManifest
) -> dict[str, object]:
    moves = [
        {
            "sourceFileName": Path(move.to_relative_path).name,
            "from": move.from_relative_path,
            "to": move.to_relative_path,
            "objectPath": affected.object_path,
        }
        for move, affected in zip(manifest.moves, manifest.affected_objects)
    ]
    source_file_names = [str(move["sourceFileName"]) for move in moves]
    first = moves[0]
    count = len(moves)
    return {
        "sourceFileName": (
            source_file_names[0] if count == 1 else f"{count}개 파일"
        ),
        "sourceFileNames": source_file_names,
        "fileCount": count,
        "confirmationToken": _confirmation_token(source_file_names),
        "from": first["from"] if count == 1 else f"{count}개 원본 경로",
        "to": first["to"] if count == 1 else f"{count}개 대상 경로",
        "objectPath": first["objectPath"] if count == 1 else f"{count}개 Wwise 객체",
        "moves": moves,
        "changelist": manifest.changelist,
        "status": manifest.status,
        "validated": (
            manifest.status == "applied"
            and _has_valid_apply_verification(manifest_path)
        ),
        "manifest": manifest_path.as_posix(),
    }


def _verification_path(manifest_path: Path) -> Path:
    return manifest_path.with_name("apply-verification.json")


def _write_apply_verification(
    output_path: Path,
    *,
    manifest_path: Path,
    validation_path: Path,
) -> None:
    document = {
        "schemaVersion": 1,
        "verifiedAt": datetime.now(UTC).isoformat(),
        "manifest": manifest_path.as_posix(),
        "manifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "validationReport": validation_path.as_posix(),
    }
    output_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _has_valid_apply_verification(manifest_path: Path) -> bool:
    verification = _read_apply_verification(manifest_path)
    return (
        verification is not None
        and verification.get("manifest") == manifest_path.as_posix()
        and verification.get("manifestSha256")
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )


def _read_apply_verification(manifest_path: Path) -> dict[str, object] | None:
    try:
        verification = json.loads(
            _verification_path(manifest_path).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(verification, dict):
        return None
    if verification.get("manifest") != manifest_path.as_posix():
        return None
    return verification


def _manifest_operation_paths(
    manifest: RollbackManifest,
) -> tuple[Path, ...]:
    root = manifest.project_root.resolve()
    paths = [
        path
        for move in manifest.moves
        for path in (
            resolve_project_path(root, move.from_relative_path),
            resolve_project_path(root, move.to_relative_path),
        )
    ]
    paths.extend(
        resolve_project_path(root, relative_path)
        for relative_path in dict.fromkeys(
            patched.relative_path for patched in manifest.patched_files
        )
    )
    return tuple(paths)


def _resolved_manifest_state(
    manifest: RollbackManifest,
) -> tuple[list[tuple[Path, Path]], list[tuple[Path, str, str]]]:
    root = manifest.project_root.resolve()
    move_paths = [
        (
            resolve_project_path(root, move.from_relative_path),
            resolve_project_path(root, move.to_relative_path),
        )
        for move in manifest.moves
    ]
    work_units: list[tuple[Path, str, str]] = []
    for relative_path in dict.fromkeys(
        patched.relative_path for patched in manifest.patched_files
    ):
        records = [
            patched
            for patched in manifest.patched_files
            if patched.relative_path == relative_path
        ]
        original_hashes = {record.original_sha256 for record in records}
        patched_hashes = {record.patched_sha256 for record in records}
        if len(original_hashes) != 1 or len(patched_hashes) != 1:
            raise GuiServiceError(
                f"Work Unit hash records are inconsistent: {relative_path}"
            )
        work_units.append(
            (
                resolve_project_path(root, relative_path),
                next(iter(original_hashes)),
                next(iter(patched_hashes)),
            )
        )
    return move_paths, work_units


def _selected_file_names(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = (value,)
    elif isinstance(value, (list, tuple)) and all(
        isinstance(item, str) for item in value
    ):
        candidates = tuple(value)
    else:
        raise GuiServiceError("선택한 파일 목록 형식이 올바르지 않습니다.")
    selected = tuple(item.strip() for item in candidates if item.strip())
    if len({item.casefold() for item in selected}) != len(selected):
        raise GuiServiceError("같은 파일을 두 번 선택할 수 없습니다.")
    return selected


def _confirmation_token(source_file_names: object) -> str:
    return "\n".join(str(name) for name in source_file_names)


def _manifest_confirmation_token(manifest: RollbackManifest) -> str:
    return _confirmation_token(
        Path(move.to_relative_path).name for move in manifest.moves
    )


def _mark_perforce_skipped(readiness: PilotReadiness) -> PilotReadiness:
    checks = tuple(
        ReadinessCheck(
            check.name,
            "pass",
            "Skipped in local test mode; no Perforce command was executed",
        )
        if check.name in {"p4-cli", "p4-connection", "p4-workspace"}
        else check
        for check in readiness.checks
    )
    return PilotReadiness(
        readiness.project_root,
        checks,
        waapi_url=readiness.waapi_url,
        waapi_transport=readiness.waapi_transport,
        waapi_issue=readiness.waapi_issue,
        p4_connection=readiness.p4_connection,
        p4_workspace_issue=None,
    )


def _waapi_endpoint(settings: Mapping[str, object]) -> tuple[str, int, str, bool]:
    url = _required_setting(settings, "waapiUrl")
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if (
        parsed.scheme not in {"ws", "wss", "http", "https"}
        or not parsed.hostname
    ):
        raise GuiServiceError(
            "WAAPI 주소는 ws://, wss://, http:// 또는 https:// 형식이어야 합니다."
        )
    secure = parsed.scheme in {"wss", "https"}
    port = parsed.port or (443 if secure else 80)
    path = parsed.path or "/waapi"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return parsed.hostname, port, path, secure


def _save_detected_connections(
    store: PortableSettingsStore,
    settings: Mapping[str, object],
    readiness: PilotReadiness,
) -> None:
    updated = dict(settings)
    if readiness.waapi_url:
        updated["waapiUrl"] = readiness.waapi_url
    if readiness.p4_connection:
        connection = readiness.p4_connection.connection
        updated.update(
            {
                "p4Port": connection.port or "",
                "p4User": connection.user or "",
                "p4Client": connection.client or "",
                "p4Charset": connection.charset or "",
            }
        )
    if updated != dict(settings):
        store.save(updated)


def _required_setting(settings: Mapping[str, object], key: str) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GuiServiceError(f"필수 설정이 비어 있습니다: {key}")
    return value.strip()

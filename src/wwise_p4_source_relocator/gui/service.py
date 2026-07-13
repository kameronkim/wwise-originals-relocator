from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import platform
import shutil
import sys

from .. import __version__
from ..applier import ApplyError, apply_single_file
from ..models import (
    RelocationPlan,
    RollbackManifest,
    ScanResult,
    ValidationResult,
)
from ..p4_client import P4Client
from ..pilot_project import find_wwise_console
from ..planner import build_relocation_plan
from ..preflight import P4WorkspaceProbe, validate_relocation_plan
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
from ..waapi_reader import WaapiError, scan_live


DEFAULT_SETTINGS: dict[str, object] = {
    "projectRoot": "",
    "objectRoot": r"\Containers\Default Work Unit\VO",
    "chapter": "CH04",
    "waapiUrl": "ws://127.0.0.1:8080/waapi",
    "p4Executable": "",
    "changelist": "",
    "offlineTestMode": False,
}


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
        ] = apply_single_file,
        rollbacker: Callable[..., ValidationResult] = rollback_manifest,
        p4_client_factory: Callable[[str], P4Client] | None = None,
        workspace_probe_factory: Callable[[str], P4WorkspaceProbe] | None = None,
    ) -> None:
        self.store = PortableSettingsStore(data_root)
        self._readiness_inspector = readiness_inspector
        self._scanner = scanner
        self._planner = planner
        self._plan_validator = plan_validator
        self._applier = applier
        self._rollbacker = rollbacker
        self._p4_client_factory = p4_client_factory or _live_p4_client
        self._workspace_probe_factory = (
            workspace_probe_factory or P4WorkspaceProbe
        )
        self._planned_plan: RelocationPlan | None = None
        self._planned_validation: ValidationResult | None = None
        self._planned_settings: tuple[str, ...] | None = None

    def initial_state(self) -> dict[str, object]:
        settings = self.store.load()
        detected_p4 = discover_p4_executable()
        if not settings["p4Executable"] and detected_p4:
            settings["p4Executable"] = detected_p4
        console = find_wwise_console()
        active_operations = self._active_manifests_for_settings(settings)
        return {
            "settings": settings,
            "system": {
                "appVersion": __version__,
                "platform": platform.system() or sys.platform,
                "portable": bool(getattr(sys, "frozen", False)),
                "dataRoot": self.store.data_root.as_posix(),
                "p4Executable": str(settings["p4Executable"]),
                "p4Detected": bool(settings["p4Executable"]),
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
        }

    def update_settings(self, values: Mapping[str, object]) -> dict[str, object]:
        self._clear_planned_state()
        return self.store.save(values)

    def run_doctor(self, values: Mapping[str, object]) -> dict[str, object]:
        self._clear_planned_state()
        settings = self.store.save(values)
        project_root = _project_root(settings)
        p4_executable = _p4_executable(settings)
        configured_waapi_url = _required_setting(settings, "waapiUrl")
        waapi_host, waapi_port, waapi_path, waapi_secure = _waapi_endpoint(settings)
        offline_test_mode = _offline_test_mode(settings)
        readiness = self._readiness_inspector(
            project_root,
            p4_executable=p4_executable,
            p4_available=True if offline_test_mode else None,
            p4_workspace=True if offline_test_mode else None,
            waapi_host=waapi_host,
            waapi_port=waapi_port,
            waapi_path=waapi_path,
            waapi_secure=waapi_secure,
            waapi_url=configured_waapi_url,
        )
        if offline_test_mode:
            readiness = _mark_perforce_skipped(readiness)
        _save_detected_waapi_url(self.store, settings, readiness)
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
        self._clear_planned_state()
        settings = self.store.save(values)
        project_root = _project_root(settings)
        p4_executable = _p4_executable(settings)
        configured_waapi_url = _required_setting(settings, "waapiUrl")
        waapi_host, waapi_port, waapi_path, waapi_secure = _waapi_endpoint(settings)
        offline_test_mode = _offline_test_mode(settings)
        readiness = self._readiness_inspector(
            project_root,
            p4_executable=p4_executable,
            p4_available=True if offline_test_mode else None,
            p4_workspace=True if offline_test_mode else None,
            waapi_host=waapi_host,
            waapi_port=waapi_port,
            waapi_path=waapi_path,
            waapi_secure=waapi_secure,
            waapi_url=configured_waapi_url,
        )
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
        _save_detected_waapi_url(self.store, settings, readiness)
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
        plan = self._planner(scan)
        probe = (
            LocalTestWorkspaceProbe()
            if offline_test_mode
            else P4WorkspaceProbe(executable=p4_executable)
        )
        validation = self._plan_validator(plan, probe=probe)
        self._planned_plan = plan
        self._planned_validation = validation
        self._planned_settings = _plan_settings_signature(settings)

        report_root = self._new_report_root("plan")
        scan_path = report_root / "scan.json"
        plan_path = report_root / "plan.json"
        plan_markdown_path = report_root / "plan.md"
        validation_path = report_root / "validation.md"
        write_json_document(scan, scan_path)
        write_json_document(plan, plan_path)
        plan_markdown_path.write_text(
            render_relocation_plan(plan), encoding="utf-8"
        )
        validation_path.write_text(
            render_validation(validation), encoding="utf-8"
        )
        counts = {
            action: sum(item.action == action for item in plan.items)
            for action in ("move-and-patch", "skip", "manual-review")
        }
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
            "reports": {
                "scan": scan_path.as_posix(),
                "planJson": plan_path.as_posix(),
                "planMarkdown": plan_markdown_path.as_posix(),
                "validation": validation_path.as_posix(),
            },
        }

    def run_apply(
        self,
        values: Mapping[str, object],
        source_file_name: str,
        confirmation: str,
    ) -> dict[str, object]:
        settings = self.store.save(values)
        project_root = _project_root(settings)
        if _offline_test_mode(settings):
            raise GuiServiceError(
                "Perforce 없는 로컬 테스트에서는 파일을 적용할 수 없습니다."
            )
        selected = source_file_name.strip()
        if not selected or confirmation != selected:
            raise GuiServiceError("선택한 파일 확인이 일치하지 않습니다.")
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
                "아직 복구되지 않은 단일 파일 작업이 있습니다. 먼저 "
                "Rollback을 완료해 주세요."
            )

        p4_executable = _p4_executable(settings)
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
                p4=self._p4_client_factory(p4_executable),
                probe=self._workspace_probe_factory(p4_executable),
            )
        except ApplyError as exc:
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
                    "reports": {"manifest": recovery_path.as_posix()},
                }
            raise GuiServiceError(
                "파일 적용을 완료하지 못했습니다. 자동 복구 결과를 "
                f"확인하세요. 세부 정보: {exc}"
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
                "Rollback 가능한 단일 파일 manifest를 정확히 하나 "
                "찾을 수 없습니다."
            )
        manifest_path, manifest = active[0]
        source_file_name = Path(manifest.moves[0].to_relative_path).name
        if confirmation != source_file_name:
            raise GuiServiceError("Rollback 파일 확인이 일치하지 않습니다.")

        result = self._rollbacker(
            manifest,
            p4=self._p4_client_factory(_p4_executable(settings)),
            manifest_path=manifest_path,
        )
        report_root = self._new_report_root("rollback")
        validation_path = report_root / "rollback-validation.md"
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
                manifest.status in {"applied", "failed"}
                and len(manifest.moves) == 1
                and len(manifest.patched_files) == 1
                and len(manifest.affected_objects) == 1
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


def _p4_executable(settings: Mapping[str, object]) -> str:
    configured = str(settings.get("p4Executable") or "").strip()
    return configured or discover_p4_executable() or "p4"


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
    return (
        str(_project_root(settings)),
        _required_setting(settings, "objectRoot"),
        _required_setting(settings, "chapter"),
        _p4_executable(settings),
        "offline" if _offline_test_mode(settings) else "perforce",
    )


def _live_p4_client(executable: str) -> P4Client:
    return P4Client(executable=executable, dry_run=False)


def _manifest_summary(
    manifest_path: Path, manifest: RollbackManifest
) -> dict[str, object]:
    move = manifest.moves[0]
    affected = manifest.affected_objects[0]
    return {
        "sourceFileName": Path(move.to_relative_path).name,
        "from": move.from_relative_path,
        "to": move.to_relative_path,
        "objectPath": affected.object_path,
        "changelist": manifest.changelist,
        "status": manifest.status,
        "manifest": manifest_path.as_posix(),
    }


def _mark_perforce_skipped(readiness: PilotReadiness) -> PilotReadiness:
    checks = tuple(
        ReadinessCheck(
            check.name,
            "pass",
            "Skipped in local test mode; no Perforce command was executed",
        )
        if check.name in {"p4-cli", "p4-workspace"}
        else check
        for check in readiness.checks
    )
    return PilotReadiness(
        readiness.project_root,
        checks,
        waapi_url=readiness.waapi_url,
        waapi_transport=readiness.waapi_transport,
        waapi_issue=readiness.waapi_issue,
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


def _save_detected_waapi_url(
    store: PortableSettingsStore,
    settings: Mapping[str, object],
    readiness: PilotReadiness,
) -> None:
    if not readiness.waapi_url or readiness.waapi_url == settings.get("waapiUrl"):
        return
    store.save({**settings, "waapiUrl": readiness.waapi_url})


def _required_setting(settings: Mapping[str, object], key: str) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GuiServiceError(f"필수 설정이 비어 있습니다: {key}")
    return value.strip()

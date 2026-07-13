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
from ..models import RelocationPlan, ScanResult, ValidationResult
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
    render_relocation_plan,
    render_validation,
    write_json_document,
)
from ..waapi_reader import scan_live


DEFAULT_SETTINGS: dict[str, object] = {
    "projectRoot": "",
    "objectRoot": r"\Containers\Default Work Unit\VO",
    "chapter": "CH04",
    "waapiUrl": "ws://127.0.0.1:8080/waapi",
    "p4Executable": "",
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


class ReadOnlyGuiService:
    def __init__(
        self,
        *,
        data_root: str | Path | None = None,
        readiness_inspector: Callable[..., PilotReadiness] = inspect_pilot_readiness,
        scanner: Callable[..., ScanResult] = scan_live,
        planner: Callable[[ScanResult], RelocationPlan] = build_relocation_plan,
        plan_validator: Callable[..., ValidationResult] = validate_relocation_plan,
    ) -> None:
        self.store = PortableSettingsStore(data_root)
        self._readiness_inspector = readiness_inspector
        self._scanner = scanner
        self._planner = planner
        self._plan_validator = plan_validator

    def initial_state(self) -> dict[str, object]:
        settings = self.store.load()
        detected_p4 = discover_p4_executable()
        if not settings["p4Executable"] and detected_p4:
            settings["p4Executable"] = detected_p4
        console = find_wwise_console()
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
                "readOnly": True,
                "apply": False,
                "rollback": False,
                "installsDependencies": False,
                "offlineTestMode": True,
            },
        }

    def update_settings(self, values: Mapping[str, object]) -> dict[str, object]:
        return self.store.save(values)

    def run_doctor(self, values: Mapping[str, object]) -> dict[str, object]:
        settings = self.store.save(values)
        project_root = _project_root(settings)
        p4_executable = _p4_executable(settings)
        waapi_host, waapi_port = _waapi_endpoint(settings)
        offline_test_mode = _offline_test_mode(settings)
        readiness = self._readiness_inspector(
            project_root,
            p4_executable=p4_executable,
            p4_available=True if offline_test_mode else None,
            p4_workspace=True if offline_test_mode else None,
            waapi_host=waapi_host,
            waapi_port=waapi_port,
        )
        if offline_test_mode:
            readiness = _mark_perforce_skipped(readiness)
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
        settings = self.store.save(values)
        project_root = _project_root(settings)
        p4_executable = _p4_executable(settings)
        waapi_host, waapi_port = _waapi_endpoint(settings)
        offline_test_mode = _offline_test_mode(settings)
        readiness = self._readiness_inspector(
            project_root,
            p4_executable=p4_executable,
            p4_available=True if offline_test_mode else None,
            p4_workspace=True if offline_test_mode else None,
            waapi_host=waapi_host,
            waapi_port=waapi_port,
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
        waapi_url = _required_setting(settings, "waapiUrl")
        scan = self._scanner(
            project_root=project_root,
            object_root=object_root,
            chapter=chapter,
            url=waapi_url,
        )
        plan = self._planner(scan)
        probe = (
            LocalTestWorkspaceProbe()
            if offline_test_mode
            else P4WorkspaceProbe(executable=p4_executable)
        )
        validation = self._plan_validator(plan, probe=probe)

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

    def _new_report_root(self, operation: str) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        path = self.store.data_root / "reports" / f"{timestamp}-{operation}"
        path.mkdir(parents=True, exist_ok=False)
        return path


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
    return PilotReadiness(readiness.project_root, checks)


def _waapi_endpoint(settings: Mapping[str, object]) -> tuple[str, int]:
    url = _required_setting(settings, "waapiUrl")
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise GuiServiceError("WAAPI 주소는 ws:// 또는 wss:// 형식이어야 합니다.")
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    return parsed.hostname, port


def _required_setting(settings: Mapping[str, object], key: str) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GuiServiceError(f"필수 설정이 비어 있습니다: {key}")
    return value.strip()

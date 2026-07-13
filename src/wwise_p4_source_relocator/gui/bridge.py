from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

from .service import GuiServiceError, ReadOnlyGuiService


LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())


class GuiApi:
    """Small, read-only JavaScript API exposed to the local GUI."""

    def __init__(self, service: ReadOnlyGuiService) -> None:
        self.service = service
        self.window: Any | None = None
        self._operation_lock = Lock()

    def bind_window(self, window: Any) -> None:
        self.window = window

    def get_initial_state(self) -> dict[str, object]:
        return self._invoke(self.service.initial_state)

    def choose_project(self) -> dict[str, object]:
        def choose() -> dict[str, object]:
            if self.window is None:
                raise GuiServiceError("프로젝트 선택 창을 열 수 없습니다.")
            import webview

            current = str(self.service.store.load().get("projectRoot") or "")
            selected = self.window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=current if Path(current).is_dir() else "",
            )
            if not selected:
                return {"cancelled": True}
            project_root = str(Path(selected[0]).resolve())
            settings = self.service.store.load()
            settings["projectRoot"] = project_root
            self.service.update_settings(settings)
            return {"cancelled": False, "projectRoot": project_root}

        return self._invoke(choose)

    def choose_p4(self) -> dict[str, object]:
        def choose() -> dict[str, object]:
            if self.window is None:
                raise GuiServiceError("Perforce 실행 파일 선택 창을 열 수 없습니다.")
            import webview

            selected = self.window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Perforce CLI (p4;p4.exe)", "All files (*.*)"),
            )
            if not selected:
                return {"cancelled": True}
            executable = str(Path(selected[0]).resolve())
            settings = self.service.store.load()
            settings["p4Executable"] = executable
            self.service.update_settings(settings)
            return {"cancelled": False, "p4Executable": executable}

        return self._invoke(choose)

    def save_settings(self, values: Mapping[str, object]) -> dict[str, object]:
        return self._invoke(self.service.update_settings, values)

    def run_doctor(self, values: Mapping[str, object]) -> dict[str, object]:
        return self._invoke_exclusive(self.service.run_doctor, values)

    def run_plan(self, values: Mapping[str, object]) -> dict[str, object]:
        return self._invoke_exclusive(self.service.run_plan, values)

    def _invoke_exclusive(self, function: Any, *args: object) -> dict[str, object]:
        if not self._operation_lock.acquire(blocking=False):
            LOGGER.warning("Rejected concurrent GUI operation: %s", function.__name__)
            return {
                "ok": False,
                "error": "다른 점검 작업이 실행 중입니다. 완료될 때까지 기다려 주세요.",
            }
        try:
            return self._invoke(function, *args)
        finally:
            self._operation_lock.release()

    @staticmethod
    def _invoke(function: Any, *args: object) -> dict[str, object]:
        operation = getattr(function, "__name__", "unknown")
        LOGGER.info("GUI operation started: %s", operation)
        try:
            result = function(*args)
        except GuiServiceError as exc:
            LOGGER.warning("GUI operation rejected: %s: %s", operation, exc)
            return {"ok": False, "error": str(exc)}
        except Exception:
            LOGGER.exception("GUI operation failed: %s", operation)
            return {
                "ok": False,
                "error": "작업을 완료하지 못했습니다. 지원 정보의 로그를 확인해 주세요.",
            }
        if isinstance(result, dict):
            LOGGER.info("GUI operation completed: %s", operation)
            return {"ok": True, **result}
        LOGGER.info("GUI operation completed: %s", operation)
        return {"ok": True, "value": result}

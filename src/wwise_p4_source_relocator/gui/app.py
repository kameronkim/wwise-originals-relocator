from __future__ import annotations

import logging
import os
from pathlib import Path
import platform
import sys
import traceback

from .. import __version__
from .bridge import GuiApi
from .service import ReadOnlyGuiService


LOGGER = logging.getLogger(__name__)


def main() -> int:
    try:
        import webview
    except ImportError:
        details = (
            "The portable GUI requires the gui optional dependency. "
            "Install this development checkout with .[gui].\n"
            + traceback.format_exc()
        )
        _write_smoke_report(details)
        print(details, file=sys.stderr)
        return 1

    index_path = Path(__file__).with_name("assets") / "index.html"
    if os.environ.get("WWISE_RELOCATOR_SMOKE_TEST") == "1":
        return _run_portable_smoke_check(index_path)

    service = ReadOnlyGuiService()
    _configure_logging(service.store.data_root)
    LOGGER.info(
        "Starting Wwise Originals Relocator %s on %s",
        __version__,
        platform.platform(),
    )
    api = GuiApi(service)
    try:
        window = webview.create_window(
            "Wwise Originals Relocator",
            url=index_path.as_uri(),
            js_api=api,
            width=1180,
            height=780,
            min_size=(820, 620),
            background_color="#182128",
            text_select=True,
            zoomable=False,
        )
        api.bind_window(window)
        webview.start()
    except Exception:
        LOGGER.exception("Desktop GUI failed to start")
        return 1
    LOGGER.info("Wwise Originals Relocator closed normally")
    return 0


def _run_portable_smoke_check(index_path: Path) -> int:
    try:
        import waapi  # noqa: F401

        for asset_name in ("index.html", "styles.css", "app.js"):
            asset_path = index_path.with_name(asset_name)
            if not asset_path.is_file() or asset_path.stat().st_size == 0:
                raise FileNotFoundError(f"GUI asset is missing: {asset_path}")
    except Exception:
        details = "Portable smoke check failed:\n" + traceback.format_exc()
        _write_smoke_report(details)
        print(details, file=sys.stderr)
        return 1
    _write_smoke_report("Portable smoke check passed.\n")
    return 0


def _write_smoke_report(contents: str) -> None:
    report_path = os.environ.get("WWISE_RELOCATOR_SMOKE_REPORT")
    if not report_path:
        return
    try:
        Path(report_path).write_text(contents, encoding="utf-8")
    except OSError:
        pass


def _configure_logging(data_root: Path) -> None:
    log_root = data_root / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_root / "relocator.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

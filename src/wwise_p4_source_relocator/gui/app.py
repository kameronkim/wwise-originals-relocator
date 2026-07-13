from __future__ import annotations

import logging
from pathlib import Path
import sys

from .bridge import GuiApi
from .service import ReadOnlyGuiService


def main() -> int:
    try:
        import webview
    except ImportError:
        print(
            "The portable GUI requires the gui optional dependency. "
            "Install this development checkout with .[gui].",
            file=sys.stderr,
        )
        return 1

    service = ReadOnlyGuiService()
    _configure_logging(service.store.data_root)
    api = GuiApi(service)
    index_path = Path(__file__).with_name("assets") / "index.html"
    window = webview.create_window(
        "Wwise Originals Relocator",
        url=index_path.as_uri(),
        js_api=api,
        width=1180,
        height=780,
        min_size=(820, 620),
        background_color="#0f1716",
        text_select=True,
        zoomable=False,
    )
    api.bind_window(window)
    webview.start()
    return 0


def _configure_logging(data_root: Path) -> None:
    log_root = data_root / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_root / "relocator.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

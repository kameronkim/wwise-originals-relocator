from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from wwise_p4_source_relocator import __version__


REPO_ROOT = Path(__file__).resolve().parents[1]


def prepare_portable_directory(app_root: str | Path) -> None:
    destination = Path(app_root).resolve()
    if not destination.is_dir():
        raise FileNotFoundError(f"Portable app directory is missing: {destination}")

    guide_template = (REPO_ROOT / "docs" / "usage-guide.html").read_text(
        encoding="utf-8"
    )
    (destination / "사용가이드.html").write_text(
        guide_template.replace("현재 릴리스", f"버전 {__version__}"),
        encoding="utf-8",
    )
    shutil.copyfile(REPO_ROOT / "LICENSE", destination / "LICENSE.txt")
    (destination / "VERSION.txt").write_text(
        f"Wwise Originals Relocator {__version__}\n",
        encoding="utf-8",
    )

    legacy_guide = destination / "사용가이드.md"
    if legacy_guide.exists():
        legacy_guide.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add operator documentation and metadata to a portable build."
    )
    parser.add_argument("--app-root", required=True, type=Path)
    args = parser.parse_args()
    prepare_portable_directory(args.app_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

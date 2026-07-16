from __future__ import annotations

import os
from pathlib import Path


def move_file_no_replace(source: Path, target: Path) -> None:
    """Move one file without ever replacing an existing target.

    Windows rename already refuses an existing destination. POSIX rename does
    not, so create the destination as a hard link with exclusive name creation
    before removing the source. Both paths stay within one project volume.
    """

    if os.name == "nt":
        os.rename(source, target)
        return

    os.link(source, target)
    try:
        source.unlink()
    except OSError:
        try:
            target.unlink()
        except OSError:
            pass
        raise

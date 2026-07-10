from __future__ import annotations

from pathlib import Path


class UnsafeProjectPath(ValueError):
    pass


def resolve_project_path(project_root: str | Path, relative_path: str) -> Path:
    root = Path(project_root).resolve()
    supplied = Path(relative_path)
    if supplied.is_absolute():
        raise UnsafeProjectPath(f"Project path must be relative: {relative_path}")
    candidate = (root / supplied).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UnsafeProjectPath(
            f"Project path escapes the project root: {relative_path}"
        ) from exc
    return candidate

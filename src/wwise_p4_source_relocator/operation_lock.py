from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from typing import BinaryIO, Iterator
import unicodedata


class ProjectOperationBusyError(RuntimeError):
    pass


class _ProjectOperationLock:
    def __init__(self, key: str, project_root: Path) -> None:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        self.path = (
            Path(tempfile.gettempdir())
            / "wwise-originals-relocator-locks"
            / f"{digest}.lock"
        )
        self.project_root = project_root
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if not self._thread_lock.acquire(blocking=False):
            raise self._busy_error()
        try:
            if self._depth == 0:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.path.open("a+b")
                try:
                    _ensure_lock_byte(handle)
                    _lock_file(handle)
                except OSError as exc:
                    handle.close()
                    raise self._busy_error() from exc
                self._handle = handle
            self._depth += 1
        except BaseException:
            self._thread_lock.release()
            raise

    def release(self) -> None:
        try:
            if self._depth <= 0:
                raise RuntimeError("Project operation lock is not held")
            self._depth -= 1
            if self._depth == 0:
                handle = self._handle
                self._handle = None
                if handle is not None:
                    try:
                        _unlock_file(handle)
                    except OSError:
                        pass
                    finally:
                        handle.close()
        finally:
            self._thread_lock.release()

    def _busy_error(self) -> ProjectOperationBusyError:
        return ProjectOperationBusyError(
            "Another relocation operation is already running for this project: "
            f"{self.project_root}"
        )


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, _ProjectOperationLock] = {}


@contextmanager
def project_operation_lock(project_root: str | Path) -> Iterator[None]:
    root = Path(project_root).expanduser().resolve()
    key = _project_lock_key(root)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _ProjectOperationLock(key, root)
            _LOCKS[key] = lock
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _project_lock_key(root: Path) -> str:
    try:
        identity = root.stat()
    except OSError:
        identity = None
    if identity is not None and identity.st_ino:
        return f"filesystem:{identity.st_dev}:{identity.st_ino}"
    normalized = unicodedata.normalize(
        "NFC",
        os.path.normcase(os.path.abspath(os.fspath(root))),
    )
    return f"path:{normalized.casefold()}"


def _ensure_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())


def _lock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

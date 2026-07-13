from __future__ import annotations

from collections import defaultdict
from multiprocessing import get_context
from pathlib import Path, PurePosixPath
from queue import Empty
from typing import Protocol
from urllib.parse import urlparse

from .models import ScanResult, SourceItem
from .readiness import waapi_websocket_is_reachable


class WaapiError(RuntimeError):
    """Raised when Wwise object discovery cannot produce a safe scan."""


class WaapiConnection(Protocol):
    def call(
        self,
        uri: str,
        args: dict[str, object],
        *,
        options: dict[str, object],
    ) -> dict[str, object]: ...


RETURN_FIELDS = [
    "id",
    "name",
    "type",
    "path",
    "owner",
    "parent",
    "filePath",
    "originalRelativeFilePath",
    "audioSource:language",
]

WAAPI_SCAN_TIMEOUT_SECONDS = 20.0


def scan_with_connection(
    connection: WaapiConnection,
    *,
    project_root: str | Path,
    object_root: str,
    chapter: str,
) -> ScanResult:
    response = connection.call(
        "ak.wwise.core.object.get",
        {
            "from": {"path": [object_root]},
            "transform": [{"select": ["descendants"]}],
        },
        options={"return": RETURN_FIELDS},
    )
    if not isinstance(response, dict):
        raise WaapiError("WAAPI object.get did not return a response object")
    records = response.get("return")
    if not isinstance(records, list):
        raise WaapiError("WAAPI object.get response did not contain a return list")
    return build_scan_result(
        records,
        project_root=project_root,
        object_root=object_root,
        chapter=chapter,
    )


def scan_live(
    *,
    project_root: str | Path,
    object_root: str,
    chapter: str,
    url: str | None = None,
    timeout_seconds: float = WAAPI_SCAN_TIMEOUT_SECONDS,
) -> ScanResult:
    endpoint = url or "ws://127.0.0.1:8080/waapi"
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise WaapiError("WAAPI URL must use ws:// or wss://")
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/waapi"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    if not waapi_websocket_is_reachable(
        parsed.hostname,
        port,
        path=path,
        secure=parsed.scheme == "wss",
        timeout=min(timeout_seconds, 1.0),
    ):
        raise WaapiError(
            "the configured address did not accept a WAAPI WebSocket connection; "
            "another application may be using the port"
        )

    return _run_bounded_live_scan(
        {
            "project_root": str(project_root),
            "object_root": object_root,
            "chapter": chapter,
            "url": endpoint,
        },
        timeout_seconds=timeout_seconds,
    )


def _scan_live_unbounded(
    *,
    project_root: str | Path,
    object_root: str,
    chapter: str,
    url: str,
) -> ScanResult:
    try:
        from waapi import WaapiClient
    except ImportError as exc:
        raise WaapiError(
            "Live scanning requires the optional waapi-client dependency"
        ) from exc

    try:
        with WaapiClient(url=url) as connection:
            return scan_with_connection(
                connection,
                project_root=project_root,
                object_root=object_root,
                chapter=chapter,
            )
    except Exception as exc:
        if isinstance(exc, WaapiError):
            raise
        raise WaapiError(f"WAAPI scan failed: {exc}") from exc


def _scan_live_worker(result_queue: object, values: dict[str, object]) -> None:
    try:
        result = _scan_live_unbounded(**values)
    except Exception as exc:
        result_queue.put(("error", str(exc)))
    else:
        result_queue.put(("ok", result.to_dict()))


def _run_bounded_live_scan(
    values: dict[str, object],
    *,
    timeout_seconds: float,
    worker: object = _scan_live_worker,
) -> ScanResult:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    context = get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=worker,
        args=(result_queue, values),
        name="wwise-waapi-scan",
        daemon=True,
    )
    process.start()
    try:
        try:
            status, payload = result_queue.get(timeout=timeout_seconds)
        except Empty as exc:
            raise WaapiError(
                f"WAAPI scan timed out after {timeout_seconds:g} seconds"
            ) from exc
    finally:
        process.join(timeout=0.5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
        result_queue.close()
        result_queue.join_thread()

    if status == "error":
        raise WaapiError(str(payload))
    if status != "ok" or not isinstance(payload, dict):
        raise WaapiError("WAAPI scan process returned an invalid response")
    try:
        return ScanResult.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise WaapiError(f"WAAPI scan returned invalid data: {exc}") from exc


def build_scan_result(
    records: list[object],
    *,
    project_root: str | Path,
    object_root: str,
    chapter: str,
) -> ScanResult:
    root = Path(project_root).resolve()
    sounds: list[dict[str, object]] = []
    sources_by_owner: dict[str, list[dict[str, object]]] = defaultdict(list)

    for raw in records:
        if not isinstance(raw, dict):
            raise WaapiError("WAAPI returned a non-object record")
        record_type = raw.get("type")
        if record_type == "Sound":
            sounds.append(raw)
        elif record_type == "AudioFileSource":
            owner = raw.get("parent") or raw.get("owner")
            if isinstance(owner, dict) and isinstance(owner.get("id"), str):
                sources_by_owner[owner["id"]].append(raw)

    items: list[SourceItem] = []
    for sound in sounds:
        guid = _required_record_string(sound, "id")
        object_path = _required_record_string(sound, "path")
        category, inferred_chapter = infer_tree_location(object_path, object_root)
        if inferred_chapter not in {None, chapter}:
            continue

        sources = sources_by_owner.get(guid, [])
        source_paths = tuple(
            normalized
            for source in sources
            if (normalized := _source_path(source)) is not None
        )
        languages = {
            language
            for source in sources
            if (language := _source_language(source)) is not None
        }
        language = next(iter(languages)) if len(languages) == 1 else None
        if language is None and len(source_paths) == 1:
            language = infer_language(source_paths[0])

        file_path = _required_record_string(sound, "filePath")
        work_unit_path = _relative_file_path(file_path, root)
        items.append(
            SourceItem(
                object_path=object_path,
                guid=guid,
                category=category,
                source_relative_paths=source_paths,
                work_unit_path=work_unit_path,
                language=language,
                chapter=inferred_chapter,
            )
        )

    return ScanResult(
        project_root=root,
        object_root=object_root,
        chapter=chapter,
        items=tuple(sorted(items, key=lambda item: item.object_path.casefold())),
    )


def infer_tree_location(
    object_path: str, object_root: str
) -> tuple[str | None, str | None]:
    path_parts = _wwise_parts(object_path)
    root_parts = _wwise_parts(object_root)
    if path_parts[: len(root_parts)] != root_parts:
        return None, None
    relative = path_parts[len(root_parts) :]
    category = relative[0] if relative else None
    chapter = relative[1] if len(relative) > 1 else None
    return category, chapter


def infer_language(source_path: str) -> str | None:
    parts = PurePosixPath(source_path).parts
    try:
        voices_index = parts.index("Voices")
    except ValueError:
        return None
    if len(parts) <= voices_index + 1:
        return None
    return parts[voices_index + 1]


def _wwise_parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.replace("/", "\\").split("\\") if part)


def _source_path(source: dict[str, object]) -> str | None:
    raw = source.get("originalRelativeFilePath")
    if not isinstance(raw, str) or not raw.strip():
        return None
    normalized = raw.strip().replace("\\", "/").lstrip("/")
    if not normalized.casefold().startswith("originals/"):
        normalized = f"Originals/{normalized}"
    return normalized


def _source_language(source: dict[str, object]) -> str | None:
    for key in ("audioSource:language", "audioSourceLanguage", "language"):
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            return value["name"]
    return None


def _relative_file_path(file_path: str, project_root: Path) -> str:
    normalized = file_path.replace("\\", "/")
    if normalized.casefold().startswith("z:/") and project_root.as_posix().startswith(
        "/"
    ):
        normalized = normalized[2:]
    candidate = Path(normalized)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(project_root).as_posix()
        except ValueError as exc:
            raise WaapiError(
                f"Work Unit {file_path} is outside project root {project_root}"
            ) from exc
    return normalized


def _required_record_string(record: dict[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise WaapiError(f"WAAPI record is missing {key}")
    return value

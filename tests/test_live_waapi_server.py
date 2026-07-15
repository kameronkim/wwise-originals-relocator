from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from wwise_p4_source_relocator.models import (
    AffectedObjectRecord,
    RollbackManifest,
)
from wwise_p4_source_relocator.validator import (
    DEFAULT_LIVE_WWISE_BATCH_SIZE,
    validate_live_wwise_manifest,
    validate_live_wwise_manifest_at_url,
)


def build_manifest(project_root: Path, count: int = 100) -> RollbackManifest:
    affected: list[AffectedObjectRecord] = []
    for index in range(count):
        relative_path = f"Originals/Voices/English(US)/Script/CH04/line_{index:03}.wav"
        source = project_root / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"RIFFvirtual")
        affected.append(
            AffectedObjectRecord(
                object_path=rf"\Containers\VO\Script\CH04\line_{index:03}",
                guid=f"{{00000000-0000-0000-0000-{index:012d}}}",
                before_source_relative_path=relative_path.replace("/Script/", "/Scenario/"),
                after_source_relative_path=relative_path,
            )
        )
    return RollbackManifest(
        created_at="2026-07-15T00:00:00+00:00",
        project_root=project_root,
        changelist="123456",
        moves=(),
        patched_files=(),
        affected_objects=tuple(affected),
        unmanaged_files_to_delete=(),
        status="awaiting-wwise-reload",
    )


class VirtualWaapiServer:
    def __init__(
        self,
        manifest: RollbackManifest,
        *,
        missing_path: str | None = None,
        duplicate_path: str | None = None,
        error: bool = False,
    ) -> None:
        self.requests: list[list[str]] = []
        self.missing_path = missing_path
        self.duplicate_path = duplicate_path
        self.error = error
        self.records = {
            affected.object_path: {
                "id": affected.guid,
                "path": affected.object_path,
                "originalRelativeFilePath": affected.after_source_relative_path,
                "originalFilePath": str(
                    manifest.project_root / affected.after_source_relative_path
                ),
            }
            for affected in manifest.affected_objects
        }
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                paths = payload["args"]["from"]["path"]
                owner.requests.append(list(paths))
                if owner.error:
                    response = {
                        "uri": "ak.wwise.virtual.failure",
                        "message": "virtual server failure",
                    }
                else:
                    records = [
                        owner.records[path]
                        for path in paths
                        if path != owner.missing_path
                    ]
                    if owner.duplicate_path in paths:
                        records.append(dict(owner.records[owner.duplicate_path]))
                    response = {"return": list(reversed(records))}
                body = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/waapi"

    def __enter__(self) -> "VirtualWaapiServer":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class LiveWaapiVirtualServerTests(unittest.TestCase):
    def test_validates_one_hundred_objects_in_order_independent_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = build_manifest(Path(directory))
            with VirtualWaapiServer(manifest) as server:
                result = validate_live_wwise_manifest_at_url(
                    manifest, url=server.url
                )

            self.assertTrue(result.is_valid)
            self.assertEqual([32, 32, 32, 4], [len(paths) for paths in server.requests])
            self.assertEqual(
                {affected.object_path for affected in manifest.affected_objects},
                {path for request in server.requests for path in request},
            )

    def test_reports_missing_and_duplicate_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = build_manifest(Path(directory), count=40)
            missing = manifest.affected_objects[3].object_path
            duplicate = manifest.affected_objects[35].object_path
            with VirtualWaapiServer(
                manifest,
                missing_path=missing,
                duplicate_path=duplicate,
            ) as server:
                result = validate_live_wwise_manifest_at_url(
                    manifest, url=server.url
                )

            issues = {(issue.code, issue.object_path) for issue in result.issues}
            self.assertIn(("wwise-object-missing", missing), issues)
            self.assertIn(("wwise-object-ambiguous", duplicate), issues)
            self.assertEqual(2, len(server.requests))

    def test_surfaces_virtual_server_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = build_manifest(Path(directory), count=1)
            with VirtualWaapiServer(manifest, error=True) as server:
                with self.assertRaisesRegex(RuntimeError, "virtual server failure"):
                    validate_live_wwise_manifest_at_url(manifest, url=server.url)

    def test_rejects_non_positive_batch_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = build_manifest(Path(directory), count=1)
            with self.assertRaisesRegex(ValueError, "must be positive"):
                validate_live_wwise_manifest(
                    manifest,
                    connection=object(),
                    batch_size=0,
                )
        self.assertEqual(32, DEFAULT_LIVE_WWISE_BATCH_SIZE)


if __name__ == "__main__":
    unittest.main()

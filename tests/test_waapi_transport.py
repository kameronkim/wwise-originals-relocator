from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.waapi_transport import (
    HttpWaapiConnection,
    WaapiCallError,
    detect_waapi_endpoint,
)


class FakeHttpResponse:
    def __init__(self, value: dict[str, object]) -> None:
        self.payload = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class HttpWaapiConnectionTests(unittest.TestCase):
    def test_posts_an_rpc_call_to_the_waapi_endpoint(self) -> None:
        response = FakeHttpResponse({"return": [{"id": "project"}]})
        with patch(
            "wwise_p4_source_relocator.waapi_transport.urlopen",
            return_value=response,
        ) as urlopen:
            result = HttpWaapiConnection(
                "http://127.0.0.1:8090/waapi"
            ).call(
                "ak.wwise.core.object.get",
                {"waql": "from type project"},
                options={"return": ["filePath"]},
            )

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual("ak.wwise.core.object.get", body["uri"])
        self.assertEqual({"waql": "from type project"}, body["args"])
        self.assertEqual({"return": ["filePath"]}, body["options"])
        self.assertEqual("project", result["return"][0]["id"])


class WaapiDetectionTests(unittest.TestCase):
    def make_project(self, root: Path) -> Path:
        project = root / "Pilot"
        project.mkdir()
        project_file = project / "Pilot.wproj"
        project_file.write_text("<WwiseDocument/>", encoding="utf-8")
        return project

    def test_falls_back_to_http_and_verifies_the_open_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            response = {"return": [{"filePath": str(project / "Pilot.wproj")}]}
            with (
                patch(
                    "wwise_p4_source_relocator.waapi_transport."
                    "waapi_websocket_is_reachable",
                    return_value=False,
                ),
                patch.object(
                    HttpWaapiConnection,
                    "call",
                    side_effect=[{"displayName": "Wwise"}, response],
                ),
            ):
                detected = detect_waapi_endpoint(
                    "ws://127.0.0.1:8080/waapi", project_root=project
                )

        self.assertIsNotNone(detected.endpoint)
        self.assertEqual("http", detected.endpoint.transport)
        self.assertEqual("http://127.0.0.1:8090/waapi", detected.endpoint.url)
        self.assertIsNone(detected.issue)

    def test_reports_an_open_wwise_modal_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            with (
                patch(
                    "wwise_p4_source_relocator.waapi_transport."
                    "waapi_websocket_is_reachable",
                    return_value=False,
                ),
                patch.object(
                    HttpWaapiConnection,
                    "call",
                    side_effect=WaapiCallError(
                        "ak.wwise.locked", "Waiting for a modal dialog"
                    ),
                ),
            ):
                detected = detect_waapi_endpoint(
                    "ws://127.0.0.1:8080/waapi", project_root=project
                )

        self.assertIsNone(detected.endpoint)
        self.assertEqual("modal-dialog", detected.issue)

    def test_rejects_a_different_project_open_in_wwise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root)
            other = root / "Other.wproj"
            response = {"return": [{"filePath": str(other)}]}
            with (
                patch(
                    "wwise_p4_source_relocator.waapi_transport."
                    "waapi_websocket_is_reachable",
                    return_value=False,
                ),
                patch.object(
                    HttpWaapiConnection,
                    "call",
                    side_effect=[{"displayName": "Wwise"}, response],
                ),
            ):
                detected = detect_waapi_endpoint(
                    "ws://127.0.0.1:8080/waapi", project_root=project
                )

        self.assertIsNone(detected.endpoint)
        self.assertEqual("project-mismatch", detected.issue)


if __name__ == "__main__":
    unittest.main()

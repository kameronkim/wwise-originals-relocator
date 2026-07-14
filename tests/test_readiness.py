from pathlib import Path
import base64
import hashlib
import shutil
import tempfile
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.p4_client import P4Connection, P4ConnectionInfo
from wwise_p4_source_relocator.readiness import (
    _p4_contains_project,
    inspect_pilot_readiness,
    render_readiness_markdown,
    waapi_websocket_is_reachable,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class FakeWebSocketConnection:
    def __init__(self, response: bytes | None = None) -> None:
        self.response = response
        self.request = b""

    def __enter__(self) -> "FakeWebSocketConnection":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def sendall(self, request: bytes) -> None:
        self.request = request

    def recv(self, _: int) -> bytes:
        if self.response is not None:
            return self.response
        key_line = next(
            line
            for line in self.request.split(b"\r\n")
            if line.startswith(b"Sec-WebSocket-Key:")
        )
        key = key_line.split(b":", 1)[1].strip()
        accept = base64.b64encode(
            hashlib.sha1(
                key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            ).digest()
        )
        return (
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n"
            b"Sec-WebSocket-Protocol: wamp.2.json\r\n\r\n"
        )


class PilotReadinessTests(unittest.TestCase):
    def test_waapi_probe_rejects_a_plain_http_service(self) -> None:
        connection = FakeWebSocketConnection(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        with patch(
            "wwise_p4_source_relocator.waapi_transport.socket.create_connection",
            return_value=connection,
        ):
            self.assertFalse(waapi_websocket_is_reachable("127.0.0.1", 8080))

    def test_waapi_probe_accepts_the_wamp_websocket_handshake(self) -> None:
        connection = FakeWebSocketConnection()
        with patch(
            "wwise_p4_source_relocator.waapi_transport.socket.create_connection",
            return_value=connection,
        ):
            self.assertTrue(waapi_websocket_is_reachable("127.0.0.1", 8080))

    def test_workspace_probe_checks_the_project_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            project_file = project_root / "Pilot.wproj"
            project_file.write_text("<WwiseDocument/>", encoding="utf-8")

            with patch("wwise_p4_source_relocator.readiness.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "mapped"

                self.assertTrue(_p4_contains_project(project_root))

            self.assertEqual(str(project_file), run.call_args.args[0][-1])

    def test_workspace_probe_uses_the_configured_p4_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            (project_root / "Pilot.wproj").write_text(
                "<WwiseDocument/>", encoding="utf-8"
            )

            with patch("wwise_p4_source_relocator.readiness.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "mapped"

                self.assertTrue(
                    _p4_contains_project(project_root, executable="/tools/p4")
                )

            self.assertEqual("/tools/p4", run.call_args.args[0][0])

    def test_workspace_probe_uses_explicit_p4v_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            project_file = project_root / "Pilot.wproj"
            project_file.write_text("<WwiseDocument/>", encoding="utf-8")
            connection = P4Connection(
                port="ssl:perforce.example.com:1666",
                user="audio.user",
                client="audio-workspace",
            )

            with patch("wwise_p4_source_relocator.readiness.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "mapped"

                self.assertTrue(
                    _p4_contains_project(project_root, connection=connection)
                )

            self.assertEqual(
                (
                    "p4",
                    "-p",
                    "ssl:perforce.example.com:1666",
                    "-u",
                    "audio.user",
                    "-c",
                    "audio-workspace",
                    "where",
                    str(project_file),
                ),
                run.call_args.args[0],
            )

    def test_readiness_uses_the_connection_resolved_by_p4_info(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "WwiseProject"
            shutil.copytree(FIXTURE_ROOT, project_root)
            (project_root / "WwiseProject.wproj").write_text(
                "<WwiseDocument/>", encoding="utf-8"
            )
            resolved = P4Connection(
                port="perforce.example.com:1666",
                user="audio.user",
                client="audio-workspace",
            )

            with (
                patch(
                    "wwise_p4_source_relocator.readiness.query_p4_connection",
                    return_value=P4ConnectionInfo(resolved),
                ),
                patch(
                    "wwise_p4_source_relocator.readiness._p4_contains_project",
                    return_value=True,
                ) as contains,
            ):
                readiness = inspect_pilot_readiness(
                    project_root,
                    p4_available=True,
                    waapi_client_available=True,
                    waapi_reachable=True,
                )

            self.assertTrue(readiness.ready)
            self.assertEqual(
                resolved,
                contains.call_args.kwargs["connection"],
            )

    def test_readiness_distinguishes_a_missing_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "WwiseProject"
            shutil.copytree(FIXTURE_ROOT, project_root)
            (project_root / "WwiseProject.wproj").write_text(
                "<WwiseDocument/>", encoding="utf-8"
            )
            connected_without_client = P4ConnectionInfo(
                P4Connection(
                    port="perforce.example.com:1666",
                    user="audio.user",
                )
            )

            with patch(
                "wwise_p4_source_relocator.readiness.query_p4_connection",
                return_value=connected_without_client,
            ):
                readiness = inspect_pilot_readiness(
                    project_root,
                    p4_available=True,
                    waapi_client_available=True,
                    waapi_reachable=True,
                )

            workspace = next(
                check
                for check in readiness.checks
                if check.name == "p4-workspace"
            )
            connection_status = next(
                check.status
                for check in readiness.checks
                if check.name == "p4-connection"
            )
            self.assertEqual("pass", connection_status)
            self.assertEqual("fail", workspace.status)
            self.assertIn("No Perforce workspace", workspace.message)
            self.assertEqual("not-configured", readiness.p4_workspace_issue)

    def test_ready_project_passes_all_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "WwiseProject"
            shutil.copytree(FIXTURE_ROOT, project_root)
            (project_root / "WwiseProject.wproj").write_text(
                "<WwiseDocument/>", encoding="utf-8"
            )

            readiness = inspect_pilot_readiness(
                project_root,
                p4_available=True,
                p4_connection_available=True,
                p4_workspace=True,
                waapi_client_available=True,
                waapi_reachable=True,
            )

            self.assertTrue(readiness.ready)
            self.assertTrue(
                all(check.status == "pass" for check in readiness.checks)
            )
            markdown = render_readiness_markdown(readiness)
            self.assertIn("Ready: yes", markdown)
            self.assertIn("Found 2 WWU source reference(s)", markdown)

    def test_empty_project_reports_actionable_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)

            readiness = inspect_pilot_readiness(
                project_root,
                p4_available=False,
                p4_connection_available=False,
                p4_workspace=False,
                waapi_client_available=False,
                waapi_reachable=False,
            )

            self.assertFalse(readiness.ready)
            failures = {
                check.name for check in readiness.checks if check.status == "fail"
            }
            self.assertEqual(
                {
                    "wwise-project",
                    "originals-wav",
                    "wwu-sources",
                    "p4-cli",
                    "p4-connection",
                    "p4-workspace",
                    "waapi-client",
                    "waapi-server",
                },
                failures,
            )


if __name__ == "__main__":
    unittest.main()

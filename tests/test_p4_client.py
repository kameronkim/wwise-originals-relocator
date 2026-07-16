from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.p4_client import (
    P4Client,
    P4CommandError,
    P4Connection,
    P4ExecutionDisabled,
    p4_creation_flags,
    parse_p4_tagged_records,
    query_p4_connection,
)


class P4ClientTests(unittest.TestCase):
    def test_move_builds_argv_without_shell_quoting(self) -> None:
        client = P4Client()

        command = client.move(
            Path("Originals/Voices/English(US)/Scenario/My File.wav"),
            Path("Originals/Voices/English(US)/Script/My File.wav"),
            changelist="123456",
        )

        self.assertEqual(
            (
                "p4",
                "move",
                "-c",
                "123456",
                str(Path("Originals/Voices/English(US)/Scenario/My File.wav")),
                str(Path("Originals/Voices/English(US)/Script/My File.wav")),
            ),
            command.argv,
        )

    def test_dry_run_is_the_default_and_refuses_execution(self) -> None:
        client = P4Client()

        with self.assertRaisesRegex(P4ExecutionDisabled, "execution is disabled"):
            client.run(client.where("Originals"))

    def test_diff_and_revert_commands_are_exact_and_changelist_scoped(self) -> None:
        client = P4Client()

        diff = client.diff("Actor-Mixer Hierarchy/Default Work Unit.wwu")
        revert = client.revert(
            "source.wav", "target.wav", changelist="123456"
        )

        self.assertEqual(
            (
                "p4",
                "diff",
                "-du",
                "Actor-Mixer Hierarchy/Default Work Unit.wwu",
            ),
            diff.argv,
        )
        self.assertEqual(
            ("p4", "revert", "-c", "123456", "source.wav", "target.wav"),
            revert.argv,
        )

    def test_opened_and_fstat_build_structured_validation_commands(self) -> None:
        client = P4Client()

        opened = client.opened(changelist="123456")
        fstat = client.fstat_opened("source.wav", "target.wav")

        self.assertEqual(("p4", "opened", "-c", "123456"), opened.argv)
        self.assertEqual(
            (
                "p4",
                "fstat",
                "-Ro",
                "-Or",
                "-T",
                "depotFile,clientFile,path,action,change,movedFile",
                "source.wav",
                "target.wav",
            ),
            fstat.argv,
        )
        self.assertTrue(fstat.tagged)

    def test_tagged_records_preserve_move_pair_fields(self) -> None:
        records = parse_p4_tagged_records(
            "... depotFile //depot/source.wav\n"
            "... clientFile C:/work/source.wav\n"
            "... action move/delete\n"
            "... change 123\n"
            "... movedFile //depot/target.wav\n"
            "... depotFile //depot/target.wav\n"
            "... clientFile C:/work/target.wav\n"
            "... action move/add\n"
            "... change 123\n"
            "... movedFile //depot/source.wav\n"
        )

        self.assertEqual(2, len(records))
        self.assertEqual("move/delete", records[0]["action"])
        self.assertEqual("//depot/source.wav", records[1]["movedFile"])

    def test_connection_context_is_added_as_global_options(self) -> None:
        client = P4Client(
            connection=P4Connection(
                port="ssl:perforce.example.com:1666",
                user="audio.user",
                client="audio-workspace",
                charset="utf8",
            )
        )

        command = client.where("C:/Work/Audio/WwiseProject/Pilot.wproj")

        self.assertEqual(
            (
                "p4",
                "-p",
                "ssl:perforce.example.com:1666",
                "-u",
                "audio.user",
                "-c",
                "audio-workspace",
                "-C",
                "utf8",
                "where",
                "C:/Work/Audio/WwiseProject/Pilot.wproj",
            ),
            command.argv,
        )

    def test_run_treats_p4_error_output_as_failure_when_exit_code_is_zero(self) -> None:
        client = P4Client(dry_run=False)
        completed = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout="error: file(s) not opened on this client.\n",
            stderr="",
        )

        with patch("subprocess.run", return_value=completed) as run:
            with self.assertRaises(P4CommandError):
                client.run(client.move("source.wav", "target.wav"))

        self.assertEqual(
            ("p4", "-s", "move", "source.wav", "target.wav"),
            run.call_args.args[0],
        )

    def test_run_removes_p4_status_prefixes_from_success_output(self) -> None:
        client = P4Client(dry_run=False)
        completed = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout="info1: opened for edit\ntext: +patched line\nexit: 0\n",
            stderr="",
        )

        with patch("subprocess.run", return_value=completed):
            result = client.run(client.diff("Default Work Unit.wwu"))

        self.assertEqual("opened for edit\n+patched line\n", result.stdout)

    def test_run_requests_and_preserves_explicit_tagged_output(self) -> None:
        client = P4Client(dry_run=False)
        completed = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "... depotFile //depot/source.wav\n"
                "... action move/delete\n"
            ),
            stderr="",
        )

        with patch("subprocess.run", return_value=completed) as run:
            result = client.run(client.fstat_opened("source.wav"))

        self.assertEqual(
            (
                "p4",
                "-ztag",
                "fstat",
                "-Ro",
                "-Or",
                "-T",
                "depotFile,clientFile,path,action,change,movedFile",
                "source.wav",
            ),
            run.call_args.args[0],
        )
        self.assertEqual(
            (
                {
                    "depotFile": "//depot/source.wav",
                    "action": "move/delete",
                },
            ),
            parse_p4_tagged_records(result.stdout),
        )

    def test_run_rejects_tagged_error_output_with_zero_exit_code(self) -> None:
        client = P4Client(dry_run=False)
        completed = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "... code error\n"
                "... severity 3\n"
                "... data file(s) not opened on this client.\n"
            ),
            stderr="",
        )

        with patch("subprocess.run", return_value=completed):
            with self.assertRaises(P4CommandError):
                client.run(client.fstat_opened("source.wav"))

    def test_run_uses_timeout_and_windowless_process_options(self) -> None:
        client = P4Client(dry_run=False, timeout=12.0)
        completed = subprocess.CompletedProcess(
            ("p4",), 0, stdout="info: mapped\n", stderr=""
        )

        with patch("subprocess.run", return_value=completed) as run:
            client.run(client.where("C:/Work/Audio"))

        self.assertEqual(12.0, run.call_args.kwargs["timeout"])
        self.assertEqual(p4_creation_flags(), run.call_args.kwargs["creationflags"])

    def test_windows_processes_use_create_no_window(self) -> None:
        with patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True):
            self.assertEqual(0x08000000, p4_creation_flags("nt"))

    def test_query_connection_reads_effective_p4v_context(self) -> None:
        info_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "... userName audio.user\n"
                "... clientName audio-workspace\n"
                "... serverAddress ssl:perforce.example.com:1666\n"
                "... serverVersion P4D/NTX64/2026.1\n"
            ),
            stderr="",
        )
        set_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "P4PORT=ssl:perforce.example.com:1666 (set)\n"
                "P4USER=audio.user (set)\n"
                "P4CLIENT=audio-workspace (set)\n"
            ),
            stderr="",
        )

        with patch("subprocess.run", side_effect=(info_result, set_result)) as run:
            info = query_p4_connection(cwd="C:/Work/Audio")

        self.assertEqual("audio.user", info.connection.user)
        self.assertEqual("audio-workspace", info.connection.client)
        self.assertEqual("ssl:perforce.example.com:1666", info.connection.port)
        self.assertEqual("P4D/NTX64/2026.1", info.server_version)
        self.assertEqual(("p4", "-ztag", "info"), run.call_args_list[0].args[0])
        self.assertEqual("C:/Work/Audio", run.call_args_list[0].kwargs["cwd"])
        self.assertEqual(("p4", "set"), run.call_args_list[1].args[0])

    def test_query_connection_does_not_reuse_server_address_as_p4port(self) -> None:
        info_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "... userName developer\n"
                "... clientName audio-workspace\n"
                "... serverAddress localhost.localdomain:1666\n"
            ),
            stderr="",
        )
        set_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "P4PORT=172.16.32.101:1666 (set)\n"
                "P4USER=developer (set)\n"
            ),
            stderr="",
        )

        with patch("subprocess.run", side_effect=(info_result, set_result)):
            info = query_p4_connection(cwd="C:/Work/Audio")

        self.assertEqual("172.16.32.101:1666", info.connection.port)
        self.assertEqual("localhost.localdomain:1666", info.server_address)

    def test_query_connection_redacts_secret_settings_from_failure_log(self) -> None:
        info_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "... userName audio.user\n"
                "... clientName audio-workspace\n"
                "... serverAddress ssl:perforce.example.com:1666\n"
            ),
            stderr="",
        )
        set_result = subprocess.CompletedProcess(
            ("p4",),
            1,
            stdout=(
                "P4PORT=ssl:perforce.example.com:1666 (set);"
                "P4PASSWD=super-secret-ticket (set)\n"
                "P4TICKETS=C:/Users/audio.user/p4tickets.txt (set);"
                "P4CONFIG=private-config.txt (set)\n"
            ),
            stderr="error: unable to read settings\n",
        )

        with patch("subprocess.run", side_effect=(info_result, set_result)):
            with self.assertLogs(
                "wwise_p4_source_relocator.p4_client", level="WARNING"
            ) as captured:
                info = query_p4_connection(cwd="C:/Work/Audio")

        log_output = "\n".join(captured.output)
        self.assertEqual("audio-workspace", info.connection.client)
        self.assertIn("P4PORT=ssl:perforce.example.com:1666", log_output)
        self.assertIn("P4PASSWD=<redacted>", log_output)
        self.assertIn("P4TICKETS=<redacted>", log_output)
        self.assertIn("P4CONFIG=<redacted>", log_output)
        self.assertNotIn("super-secret-ticket", log_output)
        self.assertNotIn("C:/Users/audio.user/p4tickets.txt", log_output)
        self.assertNotIn("private-config.txt", log_output)

    def test_query_connection_selects_unique_workspace_for_project(self) -> None:
        info_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "... userName developer\n"
                "... clientName *unknown*\n"
                "... serverAddress localhost.localdomain:1666\n"
            ),
            stderr="",
        )
        set_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "P4PORT=172.16.32.101:1666 (set)\n"
                "P4USER=developer (set)\n"
            ),
            stderr="",
        )
        clients_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "... client audio-workspace\n"
                "... Host test-host\n"
                "... client other-workspace\n"
                "... Host other-host\n"
            ),
            stderr="",
        )
        client_spec_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout=(
                "... Client audio-workspace\n"
                "... Root /work/audio\n"
            ),
            stderr="",
        )
        where_result = subprocess.CompletedProcess(
            ("p4",),
            0,
            stdout="//depot/project //audio-workspace/project /work/audio/project\n",
            stderr="",
        )

        with patch(
            "subprocess.run",
            side_effect=(
                info_result,
                set_result,
                clients_result,
                client_spec_result,
                where_result,
            ),
        ) as run, patch("socket.gethostname", return_value="test-host"):
            info = query_p4_connection(cwd="/work/audio/project")

        self.assertEqual("audio-workspace", info.connection.client)
        self.assertEqual(("audio-workspace",), info.client_candidates)
        self.assertIn("client", run.call_args_list[3].args[0])
        self.assertIn("-c", run.call_args_list[4].args[0])
        self.assertIn("audio-workspace", run.call_args_list[4].args[0])


if __name__ == "__main__":
    unittest.main()

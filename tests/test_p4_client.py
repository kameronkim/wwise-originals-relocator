from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from wwise_p4_source_relocator.p4_client import (
    P4Client,
    P4CommandError,
    P4ExecutionDisabled,
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


if __name__ == "__main__":
    unittest.main()

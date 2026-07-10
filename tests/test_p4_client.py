from pathlib import Path
import unittest

from wwise_p4_source_relocator.p4_client import (
    P4Client,
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
                "Originals/Voices/English(US)/Scenario/My File.wav",
                "Originals/Voices/English(US)/Script/My File.wav",
            ),
            command.argv,
        )

    def test_dry_run_is_the_default_and_refuses_execution(self) -> None:
        client = P4Client()

        with self.assertRaisesRegex(P4ExecutionDisabled, "execution is disabled"):
            client.run(client.where("Originals"))


if __name__ == "__main__":
    unittest.main()

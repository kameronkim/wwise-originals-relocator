from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from wwise_p4_source_relocator.p4_client import (
    P4Client,
    P4Connection,
    parse_p4_tagged_records,
)
from wwise_p4_source_relocator.applier import apply_single_file
from wwise_p4_source_relocator.models import RelocationPlan, RelocationPlanItem
from wwise_p4_source_relocator.preflight import P4WorkspaceProbe
from wwise_p4_source_relocator.rollback import rollback_manifest
from wwise_p4_source_relocator.validator import _validate_perforce_opened_state


LIVE_TEST_ENABLED = os.environ.get("WWISE_RELOCATOR_LIVE_P4") == "1"
P4_EXE = os.environ.get("WWISE_RELOCATOR_TEST_P4", "p4")
P4D_EXE = os.environ.get("WWISE_RELOCATOR_TEST_P4D", "p4d")
EVIDENCE_PATH = os.environ.get("WWISE_RELOCATOR_P4_EVIDENCE")
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


@unittest.skipUnless(
    LIVE_TEST_ENABLED,
    "set WWISE_RELOCATOR_LIVE_P4=1 to run against disposable p4d",
)
class LiveP4ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix=".live-p4-",
            dir=Path.cwd(),
        )
        self.temp_root = Path(self.temp.name)
        self.server_root = self.temp_root / "server"
        self.workspace_root = self.temp_root / "Workspace With Spaces"
        self.server_root.mkdir()
        self.workspace_root.mkdir()
        self.port = f"127.0.0.1:{_unused_tcp_port()}"
        self.user = "relocator-test-user"
        self.client_name = "relocator-windows-test"
        self.server_log = self.temp_root / "p4d.log"
        self.server = subprocess.Popen(
            (
                P4D_EXE,
                "-r",
                str(self.server_root),
                "-p",
                self.port,
                "-L",
                str(self.server_log),
                "-J",
                "off",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
        self._wait_for_server()
        self._create_client()

    def tearDown(self) -> None:
        if hasattr(self, "server"):
            self.server.terminate()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)
        self.temp.cleanup()

    def test_app_fstat_command_reports_move_pair_and_edit(self) -> None:
        project = self.workspace_root / "Ilias_WwiseProject"
        source = (
            project
            / "Originals/Voices/English(US)/Scenario/CH04/CH04_S101_SQ_001.wav"
        )
        target = (
            project
            / "Originals/Voices/English(US)/Cutscene/CH04/CH04_S101_SQ_001.wav"
        )
        work_unit = project / "Containers/Temp_VO.wwu"
        source.parent.mkdir(parents=True)
        work_unit.parent.mkdir(parents=True)
        source.write_bytes(b"RIFF-live-p4-integration")
        work_unit.write_text("<WorkUnit>before</WorkUnit>\n", encoding="utf-8")

        self._p4("reconcile", "-a", str(project / "..."))
        self._p4("submit", "-d", "Seed relocator fstat integration fixture")
        self._p4("edit", str(work_unit))
        self._p4("edit", str(source))
        target.parent.mkdir(parents=True)
        self._p4("move", str(source), str(target))
        work_unit.write_text("<WorkUnit>after</WorkUnit>\n", encoding="utf-8")

        connection = P4Connection(
            port=self.port,
            user=self.user,
            client=self.client_name,
        )
        client = P4Client(
            executable=P4_EXE,
            connection=connection,
            dry_run=False,
        )
        paths = (source, target, work_unit)
        app_command = client.fstat_opened(*paths)
        legacy_status_only = _run(
            (app_command.argv[0], "-s", *app_command.argv[1:])
        )
        runtime_argv = (
            app_command.argv[0],
            *(("-ztag",) if app_command.tagged else ("-s",)),
            *app_command.argv[1:],
        )
        raw_app = _run(runtime_argv)
        app_result = client.run(app_command)
        app_records = parse_p4_tagged_records(app_result.stdout)
        tagged = self._p4(
            "-ztag",
            "fstat",
            "-Ro",
            "-Or",
            "-T",
            "depotFile,clientFile,path,action,change,movedFile",
            *(str(path) for path in paths),
        )
        tagged_with_local_path = self._p4(
            "-ztag",
            "fstat",
            "-Ro",
            "-Or",
            "-Op",
            "-T",
            "depotFile,clientFile,path,action,change,movedFile",
            *(str(path) for path in paths),
        )
        opened = self._p4(
            "-ztag",
            "opened",
            *(str(path) for path in paths),
        )
        issues, summary = _validate_perforce_opened_state(
            resolved_moves=((source, target),),
            resolved_work_units=(work_unit,),
            p4=client,
        )

        self._p4("revert", str(source), str(target), str(work_unit))
        e2e_project = self.workspace_root / "EndToEnd_WwiseProject"
        shutil.copytree(FIXTURE_ROOT, e2e_project)
        self._p4("reconcile", "-a", str(e2e_project / "..."))
        self._p4("submit", "-d", "Seed end-to-end apply fixture")
        manifest_path = self.temp_root / "rollback-manifest.json"
        end_to_end_error = ""
        end_to_end_status = ""
        end_to_end_validation: dict[str, object] = {}
        rollback_valid = False
        try:
            manifest, validation = apply_single_file(
                _build_plan(e2e_project),
                only="CH04_S102_WT_001.wav",
                manifest_path=manifest_path,
                p4=client,
                probe=P4WorkspaceProbe(
                    executable=P4_EXE,
                    connection=connection,
                ),
            )
            end_to_end_status = manifest.status
            end_to_end_validation = validation.details or {}
            rollback_valid = rollback_manifest(
                manifest,
                p4=client,
                manifest_path=manifest_path,
            ).is_valid
        except Exception as exc:  # evidence must survive a regression failure
            end_to_end_error = f"{type(exc).__name__}: {exc}"

        evidence = {
            "platform": os.name,
            "serverVersion": self._p4("-ztag", "info").stdout,
            "appCommand": list(runtime_argv),
            "legacyStatusOnly": _completed_process_dict(legacy_status_only),
            "rawAppStyle": _completed_process_dict(raw_app),
            "strippedAppStyle": app_result.stdout,
            "parsedAppRecords": app_records,
            "explicitTagged": _completed_process_dict(tagged),
            "explicitTaggedWithOp": _completed_process_dict(
                tagged_with_local_path
            ),
            "taggedOpened": _completed_process_dict(opened),
            "validatorIssues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "objectPath": issue.object_path,
                }
                for issue in issues
            ],
            "validatorSummary": summary,
            "endToEnd": {
                "status": end_to_end_status,
                "validation": end_to_end_validation,
                "rollbackValid": rollback_valid,
                "error": end_to_end_error,
            },
        }
        evidence_path = Path(
            EVIDENCE_PATH or self.temp_root / "perforce-fstat-evidence.json"
        )
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Perforce fstat evidence: {evidence_path}")
        print(json.dumps(evidence, indent=2, ensure_ascii=False))

        self.assertEqual(3, len(app_records), evidence_path.read_text("utf-8"))
        self.assertEqual((), issues, evidence_path.read_text("utf-8"))
        self.assertEqual(
            "awaiting-wwise-reload",
            end_to_end_status,
            evidence_path.read_text("utf-8"),
        )
        self.assertTrue(rollback_valid, evidence_path.read_text("utf-8"))
        self.assertEqual(
            {
                "moveAddCount": 1,
                "moveDeleteCount": 1,
                "movePairCount": 1,
                "workUnitEditCount": 1,
            },
            {
                key: summary[key]
                for key in (
                    "moveAddCount",
                    "moveDeleteCount",
                    "movePairCount",
                    "workUnitEditCount",
                )
            },
        )

    def _wait_for_server(self) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = _run(
                (P4_EXE, "-p", self.port, "-u", self.user, "info")
            )
            if result.returncode == 0:
                return
            if self.server.poll() is not None:
                break
            time.sleep(0.2)
        log = self.server_log.read_text(encoding="utf-8", errors="replace")
        self.fail(f"p4d did not become ready on {self.port}\n{log}")

    def _create_client(self) -> None:
        root = str(self.workspace_root)
        spec = (
            f"Client: {self.client_name}\n"
            f"Owner: {self.user}\n"
            f"Root: {root}\n"
            "Options: noallwrite noclobber nocompress unlocked nomodtime normdir\n"
            "LineEnd: local\n"
            "View:\n"
            f"\t//depot/... //{self.client_name}/...\n"
        )
        result = _run(
            (P4_EXE, "-p", self.port, "-u", self.user, "client", "-i"),
            input_text=spec,
        )
        if result.returncode != 0:
            self.fail(f"client creation failed\n{result.stdout}\n{result.stderr}")

    def _p4(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = _run(
            (
                P4_EXE,
                "-p",
                self.port,
                "-u",
                self.user,
                "-c",
                self.client_name,
                *args,
            )
        )
        if result.returncode != 0:
            self.fail(
                f"p4 command failed: {' '.join(args)}\n"
                f"{result.stdout}\n{result.stderr}"
            )
        return result


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _build_plan(project_root: Path) -> RelocationPlan:
    return RelocationPlan(
        project_root=project_root,
        object_root=r"\Containers\Default Work Unit\VO\Temp_VO",
        chapter="CH04",
        items=(
            RelocationPlanItem(
                object_path=(
                    r"\Containers\Default Work Unit\VO\Temp_VO\Script\CH04"
                    r"\CH04_S102_WT_001"
                ),
                guid="{8886C06E-4664-4CEA-B3F1-8668CCDF3683}",
                source_file_name="CH04_S102_WT_001.wav",
                from_relative_path=(
                    "Originals/Voices/English(US)/Scenario/CH04/"
                    "CH04_S102_WT_001.wav"
                ),
                to_relative_path=(
                    "Originals/Voices/English(US)/Script/CH04/"
                    "CH04_S102_WT_001.wav"
                ),
                work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
                action="move-and-patch",
            ),
        ),
    )


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run(
    argv: tuple[str, ...], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        creationflags=_creation_flags(),
    )


def _completed_process_dict(
    result: subprocess.CompletedProcess[str],
) -> dict[str, object]:
    return {
        "returnCode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }

from __future__ import annotations

import hashlib
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
from wwise_p4_source_relocator.applier import apply_selected_files
from wwise_p4_source_relocator.models import RelocationPlan, RelocationPlanItem
from wwise_p4_source_relocator.preflight import P4WorkspaceProbe
from wwise_p4_source_relocator.report import read_rollback_manifest
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
        self._original_p4passwd = os.environ.pop("P4PASSWD", None)
        self._original_p4tickets = os.environ.get("P4TICKETS")
        self.addCleanup(self._restore_p4_environment)
        self.temp = tempfile.TemporaryDirectory(
            prefix=".live-p4-",
            dir=Path.cwd(),
        )
        self.addCleanup(self.temp.cleanup)
        self.temp_root = Path(self.temp.name)
        os.environ["P4TICKETS"] = str(self.temp_root / "tickets")
        self.server_root = self.temp_root / "server"
        self.workspace_root = self.temp_root / "Workspace With Spaces"
        self.server_root.mkdir()
        self.workspace_root.mkdir()
        self.port = f"127.0.0.1:{_unused_tcp_port()}"
        self.user = "relocator-test-user"
        self.client_name = "relocator-windows-test"
        self.server_log = self.temp_root / "p4d.log"
        self._start_server()
        self.addCleanup(self._stop_server)
        self._wait_for_server()
        if not self._create_client():
            self._bootstrap_secure_server()
            if not self._create_client():
                self.fail(
                    "client creation failed after secure-server bootstrap\n"
                    f"{self._client_creation_error}"
                )

    def _start_server(self) -> None:
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

    def _restore_p4_environment(self) -> None:
        if self._original_p4passwd is None:
            os.environ.pop("P4PASSWD", None)
        else:
            os.environ["P4PASSWD"] = self._original_p4passwd
        if self._original_p4tickets is None:
            os.environ.pop("P4TICKETS", None)
        else:
            os.environ["P4TICKETS"] = self._original_p4tickets

    def test_app_fstat_command_reports_move_pair_and_edit(self) -> None:
        project = self.workspace_root / "WwiseTestProject"
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
        second_source = (
            e2e_project
            / "Originals/Voices/English(US)/Dialog/CH04/CH04_D001_WT_001.wav"
        )
        second_source.parent.mkdir(parents=True, exist_ok=True)
        second_source.write_bytes(b"RIFF-live-p4-second-wave")
        self._p4("reconcile", "-a", str(e2e_project / "..."))
        self._p4("submit", "-d", "Seed end-to-end apply fixture")
        manifest_path = self.temp_root / "rollback-manifest.json"
        e2e_plan = _build_plan(e2e_project)
        selected_names = tuple(item.source_file_name for item in e2e_plan.items)
        source_paths = tuple(
            e2e_project / str(item.from_relative_path) for item in e2e_plan.items
        )
        target_paths = tuple(
            e2e_project / str(item.to_relative_path) for item in e2e_plan.items
        )
        e2e_work_unit = (
            e2e_project / "Actor-Mixer Hierarchy/Default Work Unit.wwu"
        )
        original_source_hashes = tuple(_sha256(path) for path in source_paths)
        original_work_unit_hash = _sha256(e2e_work_unit)
        end_to_end_error = ""
        end_to_end_status = ""
        end_to_end_validation: dict[str, object] = {}
        manifest_move_count = 0
        apply_sources_absent = False
        apply_targets_present = False
        apply_work_unit_changed = False
        rollback_valid = False
        rollback_status = ""
        rollback_sources_restored = False
        rollback_targets_removed = False
        rollback_work_unit_restored = False
        rollback_opened_file_count = -1
        try:
            manifest, validation = apply_selected_files(
                e2e_plan,
                only=selected_names,
                manifest_path=manifest_path,
                p4=client,
                probe=P4WorkspaceProbe(
                    executable=P4_EXE,
                    connection=connection,
                ),
            )
            end_to_end_status = manifest.status
            manifest_move_count = len(manifest.moves)
            perforce_details = (validation.details or {}).get("perforce")
            if isinstance(perforce_details, dict):
                end_to_end_validation = {
                    key: perforce_details.get(key)
                    for key in (
                        "expectedMoveCount",
                        "moveAddCount",
                        "moveDeleteCount",
                        "movePairCount",
                        "expectedWorkUnitCount",
                        "workUnitEditCount",
                        "valid",
                    )
                }
            apply_sources_absent = all(
                not path.exists() for path in source_paths
            )
            apply_targets_present = all(path.is_file() for path in target_paths)
            apply_work_unit_changed = (
                _sha256(e2e_work_unit) != original_work_unit_hash
            )
            rollback_valid = rollback_manifest(
                manifest,
                p4=client,
                manifest_path=manifest_path,
            ).is_valid
            rollback_status = read_rollback_manifest(manifest_path).status
            rollback_sources_restored = all(
                path.is_file() and _sha256(path) == expected_hash
                for path, expected_hash in zip(
                    source_paths,
                    original_source_hashes,
                )
            )
            rollback_targets_removed = all(
                not path.exists() for path in target_paths
            )
            rollback_work_unit_restored = (
                _sha256(e2e_work_unit) == original_work_unit_hash
            )
            post_rollback_opened = _run(
                (
                    P4_EXE,
                    "-p",
                    self.port,
                    "-u",
                    self.user,
                    "-c",
                    self.client_name,
                    "-ztag",
                    "opened",
                    str(e2e_project / "..."),
                )
            )
            rollback_opened_file_count = len(
                [
                    record
                    for record in parse_p4_tagged_records(
                        post_rollback_opened.stdout
                    )
                    if record.get("depotFile") or record.get("clientFile")
                ]
            )
        except Exception as exc:  # evidence must survive a regression failure
            end_to_end_error = type(exc).__name__

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
                "selectedFileCount": len(selected_names),
                "manifestMoveCount": manifest_move_count,
                "statusBeforeRollback": end_to_end_status,
                "perforce": end_to_end_validation,
                "apply": {
                    "sourcesAbsent": apply_sources_absent,
                    "targetsPresent": apply_targets_present,
                    "workUnitChanged": apply_work_unit_changed,
                },
                "rollback": {
                    "valid": rollback_valid,
                    "manifestStatus": rollback_status,
                    "sourcesRestored": rollback_sources_restored,
                    "targetsRemoved": rollback_targets_removed,
                    "workUnitHashRestored": rollback_work_unit_restored,
                    "openedFileCount": rollback_opened_file_count,
                },
                "errorType": end_to_end_error,
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
        self.assertEqual(2, manifest_move_count, evidence_path.read_text("utf-8"))
        self.assertTrue(
            apply_sources_absent,
            evidence_path.read_text("utf-8"),
        )
        self.assertTrue(
            apply_targets_present,
            evidence_path.read_text("utf-8"),
        )
        self.assertTrue(
            apply_work_unit_changed,
            evidence_path.read_text("utf-8"),
        )
        self.assertEqual(
            {
                "moveAddCount": 2,
                "moveDeleteCount": 2,
                "movePairCount": 2,
                "workUnitEditCount": 1,
            },
            {
                key: end_to_end_validation.get(key)
                for key in (
                    "moveAddCount",
                    "moveDeleteCount",
                    "movePairCount",
                    "workUnitEditCount",
                )
            },
            evidence_path.read_text("utf-8"),
        )
        self.assertTrue(rollback_valid, evidence_path.read_text("utf-8"))
        self.assertEqual(
            "rolled-back",
            rollback_status,
            evidence_path.read_text("utf-8"),
        )
        self.assertTrue(
            rollback_sources_restored,
            evidence_path.read_text("utf-8"),
        )
        self.assertTrue(
            rollback_targets_removed,
            evidence_path.read_text("utf-8"),
        )
        self.assertTrue(
            rollback_work_unit_restored,
            evidence_path.read_text("utf-8"),
        )
        self.assertEqual(
            0,
            rollback_opened_file_count,
            evidence_path.read_text("utf-8"),
        )
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

    def _create_client(self) -> bool:
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
        if result.returncode == 0:
            return True
        output = f"{result.stdout}\n{result.stderr}"
        self._client_creation_error = output
        if "P4PASSWD" in output:
            return False
        self.fail(f"client creation failed\n{result.stdout}\n{result.stderr}")

    def _bootstrap_secure_server(self) -> None:
        self._stop_server()
        for setting in (
            "dm.user.noautocreate=0",
            "dm.user.setinitialpasswd=1",
            "dm.user.resetpassword=0",
            "run.users.authorize=0",
            "dm.user.hideinvalid=0",
        ):
            result = _run(
                (
                    P4D_EXE,
                    "-r",
                    str(self.server_root),
                    f"-cset {setting}",
                )
            )
            if result.returncode != 0:
                self.fail(
                    f"secure-server bootstrap failed: {setting}\n"
                    f"{result.stdout}\n{result.stderr}"
                )
        self._start_server()
        self._wait_for_server()
        password = "disposable-relocator-test"
        passwd = _run(
            (P4_EXE, "-p", self.port, "-u", self.user, "passwd"),
            input_text=f"{password}\n{password}\n",
        )
        if passwd.returncode != 0:
            self.fail(
                "first-user password bootstrap failed\n"
                f"{passwd.stdout}\n{passwd.stderr}"
            )
        os.environ["P4PASSWD"] = password
        login = _run(
            (P4_EXE, "-p", self.port, "-u", self.user, "login"),
            input_text=f"{password}\n",
        )
        if login.returncode != 0:
            self.fail(
                "first-user login bootstrap failed\n"
                f"{login.stdout}\n{login.stderr}"
            )

    def _stop_server(self) -> None:
        if not hasattr(self, "server") or self.server.poll() is not None:
            return
        self.server.terminate()
        try:
            self.server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait(timeout=5)

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
            RelocationPlanItem(
                object_path=(
                    r"\Containers\Default Work Unit\VO\Temp_VO\Script\CH04"
                    r"\CH04_D001_WT_001"
                ),
                guid="{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}",
                source_file_name="CH04_D001_WT_001.wav",
                from_relative_path=(
                    "Originals/Voices/English(US)/Dialog/CH04/"
                    "CH04_D001_WT_001.wav"
                ),
                to_relative_path=(
                    "Originals/Voices/English(US)/Script/CH04/"
                    "CH04_D001_WT_001.wav"
                ),
                work_unit_path="Actor-Mixer Hierarchy/Default Work Unit.wwu",
                action="move-and-patch",
            ),
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

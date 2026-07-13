from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Literal

from .wwise_xml import WwuParseError, parse_source_references


CheckStatus = Literal["pass", "fail"]


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    status: CheckStatus
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "message": self.message}


@dataclass(frozen=True, slots=True)
class PilotReadiness:
    project_root: Path
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return all(check.status == "pass" for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "projectRoot": self.project_root.as_posix(),
            "ready": self.ready,
            "checks": [check.to_dict() for check in self.checks],
        }


def inspect_pilot_readiness(
    project_root: str | Path,
    *,
    p4_available: bool | None = None,
    p4_workspace: bool | None = None,
    waapi_client_available: bool | None = None,
    waapi_reachable: bool | None = None,
    waapi_host: str = "127.0.0.1",
    waapi_port: int = 8080,
) -> PilotReadiness:
    root = Path(project_root).resolve()
    checks: list[ReadinessCheck] = []

    root_exists = root.is_dir()
    checks.append(
        _check(
            "project-root",
            root_exists,
            f"Project root exists: {root}" if root_exists else f"Project root is missing: {root}",
        )
    )
    project_files = sorted(root.glob("*.wproj")) if root_exists else []
    checks.append(
        _check(
            "wwise-project",
            len(project_files) == 1,
            f"Found Wwise project: {project_files[0].name}"
            if len(project_files) == 1
            else f"Expected one .wproj file, found {len(project_files)}",
        )
    )

    originals = root / "Originals"
    wav_files = list(originals.rglob("*.wav")) if originals.is_dir() else []
    checks.append(
        _check(
            "originals-wav",
            bool(wav_files),
            f"Found {len(wav_files)} WAV source(s) under Originals"
            if wav_files
            else "No WAV sources were found under Originals",
        )
    )

    work_units = sorted(root.rglob("*.wwu")) if root_exists else []
    source_count = 0
    parse_errors = 0
    for work_unit in work_units:
        try:
            source_count += len(
                parse_source_references(work_unit, project_root=root)
            )
        except WwuParseError:
            parse_errors += 1
    checks.append(
        _check(
            "wwu-sources",
            source_count > 0 and parse_errors == 0,
            f"Found {source_count} WWU source reference(s)"
            if source_count > 0 and parse_errors == 0
            else (
                f"Found {source_count} source reference(s) with {parse_errors} parse error(s)"
            ),
        )
    )

    detected_p4 = shutil.which("p4") is not None if p4_available is None else p4_available
    checks.append(
        _check(
            "p4-cli",
            detected_p4,
            "p4 CLI is available" if detected_p4 else "p4 CLI is not available",
        )
    )
    if p4_workspace is None:
        in_workspace = _p4_contains_project(root) if detected_p4 and root_exists else False
    else:
        in_workspace = p4_workspace
    checks.append(
        _check(
            "p4-workspace",
            in_workspace,
            "Project root is mapped in the current Perforce workspace"
            if in_workspace
            else "Project root is not mapped in the current Perforce workspace",
        )
    )

    client_available = (
        importlib.util.find_spec("waapi") is not None
        if waapi_client_available is None
        else waapi_client_available
    )
    checks.append(
        _check(
            "waapi-client",
            client_available,
            "waapi-client is installed"
            if client_available
            else "waapi-client is not installed",
        )
    )
    reachable = (
        _port_is_reachable(waapi_host, waapi_port)
        if waapi_reachable is None
        else waapi_reachable
    )
    checks.append(
        _check(
            "waapi-server",
            reachable,
            f"WAAPI is reachable at {waapi_host}:{waapi_port}"
            if reachable
            else f"WAAPI is not reachable at {waapi_host}:{waapi_port}",
        )
    )
    return PilotReadiness(root, tuple(checks))


def render_readiness_markdown(readiness: PilotReadiness) -> str:
    lines = [
        "# Pilot Readiness",
        "",
        f"- Ready: {'yes' if readiness.ready else 'no'}",
        f"- Project root: `{readiness.project_root}`",
        "",
        "## Checks",
        "",
        "| Check | Status | Details |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| {check.name} | {check.status} | {_escape(check.message)} |"
        for check in readiness.checks
    )
    return "\n".join(lines) + "\n"


def _check(name: str, passed: bool, message: str) -> ReadinessCheck:
    return ReadinessCheck(name, "pass" if passed else "fail", message)


def _p4_contains_project(project_root: Path) -> bool:
    project_file = next(project_root.glob("*.wproj"), project_root)
    try:
        result = subprocess.run(
            ("p4", "where", str(project_file)),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0 and "not in client view" not in result.stdout


def _port_is_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")

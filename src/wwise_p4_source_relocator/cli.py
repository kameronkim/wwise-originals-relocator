from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from .applier import ApplyError, apply_single_file
from .p4_client import P4Client
from .planner import build_noop_plan, build_relocation_plan
from .pilot_project import PilotProjectError, create_pilot_project, find_wwise_console
from .preflight import validate_relocation_plan
from .readiness import inspect_pilot_readiness, render_readiness_markdown
from .report import (
    read_relocation_plan,
    read_rollback_manifest,
    read_scan_result,
    render_relocation_plan,
    render_validation,
    write_json_document,
    write_json_plan,
    write_markdown_plan,
)
from .rollback import rollback_local_manifest, rollback_manifest
from .validator import (
    validate_applied_filesystem_manifest,
    validate_applied_manifest,
    validate_live_wwise_manifest_at_url,
)
from .waapi_reader import scan_live
from .wwise_xml import parse_source_references


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wwise-p4-source-relocator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser(
        "inspect-wwu", help="Parse a WWU and write an inspection-only plan"
    )
    inspect.add_argument("--wwu", required=True, type=Path)
    inspect.add_argument("--project-root", required=True, type=Path)
    inspect.add_argument("--json-out", required=True, type=Path)
    inspect.add_argument("--markdown-out", required=True, type=Path)

    scan = subparsers.add_parser("scan", help="Read Wwise objects and source paths")
    scan.add_argument("--project-root", required=True, type=Path)
    scan.add_argument("--object-root", required=True)
    scan.add_argument("--chapter", required=True)
    scan.add_argument("--out", required=True, type=Path)
    scan.add_argument("--waapi-url")

    plan = subparsers.add_parser("plan", help="Build a dry-run relocation plan")
    plan.add_argument("--scan", required=True, type=Path)
    plan.add_argument(
        "--rule",
        default="tree-category-to-originals-folder",
        choices=("tree-category-to-originals-folder",),
    )
    plan.add_argument("--out", required=True, type=Path)
    plan.add_argument("--markdown-out", type=Path)

    validate = subparsers.add_parser(
        "validate-plan", help="Run filesystem and Perforce preflight checks"
    )
    validate.add_argument("--plan", required=True, type=Path)
    validate.add_argument("--report", type=Path)

    apply = subparsers.add_parser(
        "apply", help="Apply exactly one preflighted relocation candidate"
    )
    apply.add_argument("--plan", required=True, type=Path)
    apply.add_argument("--only", required=True)
    apply.add_argument("--manifest", required=True, type=Path)

    validate_apply = subparsers.add_parser(
        "validate-apply", help="Validate an applied relocation from its manifest"
    )
    validate_apply.add_argument("--manifest", required=True, type=Path)
    validate_apply.add_argument("--report", type=Path)
    validate_apply.add_argument("--waapi-url")

    rollback = subparsers.add_parser(
        "rollback", help="Revert only files recorded in a rollback manifest"
    )
    rollback.add_argument("--manifest", required=True, type=Path)

    doctor = subparsers.add_parser(
        "doctor", help="Check whether a Wwise project is ready for a pilot"
    )
    doctor.add_argument("--project-root", required=True, type=Path)
    doctor.add_argument("--json-out", type=Path)
    doctor.add_argument("--markdown-out", type=Path)
    doctor.add_argument("--waapi-host", default="127.0.0.1")
    doctor.add_argument("--waapi-port", default=8080, type=int)

    bootstrap = subparsers.add_parser(
        "bootstrap-project", help="Create a disposable populated Wwise pilot project"
    )
    bootstrap.add_argument("--project-root", required=True, type=Path)
    bootstrap.add_argument("--wwise-console", type=Path)
    bootstrap.add_argument("--project-name", default="WwiseRelocatorPilot")
    bootstrap.add_argument("--platform", default="Mac")
    bootstrap.add_argument("--language", default="English(US)")
    bootstrap.add_argument(
        "--object-root",
        default=r"\Containers\Default Work Unit\VO\Temp_VO",
    )
    bootstrap.add_argument(
        "--category",
        help=(
            "Create one custom category fixture instead of the default "
            "Script, Dialog, and Cutscene set"
        ),
    )
    bootstrap.add_argument("--chapter", default="CH04")
    bootstrap.add_argument("--source-category", default="Scenario")
    bootstrap.add_argument(
        "--sound-name",
        help="Sound name for a single custom fixture",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inspect-wwu":
        references = parse_source_references(args.wwu, project_root=args.project_root)
        plan = build_noop_plan(args.project_root, references)
        write_json_plan(plan, args.json_out)
        write_markdown_plan(plan, args.markdown_out)
        print(
            f"Discovered {len(references)} source reference(s); "
            "wrote a no-op plan and performed no mutations."
        )
        return 0
    if args.command == "scan":
        result = scan_live(
            project_root=args.project_root,
            object_root=args.object_root,
            chapter=args.chapter,
            url=args.waapi_url,
        )
        write_json_document(result, args.out)
        print(f"Scanned {len(result.items)} Wwise object(s); wrote {args.out}.")
        return 0
    if args.command == "plan":
        scan_result = read_scan_result(args.scan)
        plan = build_relocation_plan(scan_result)
        write_json_document(plan, args.out)
        markdown_path = args.markdown_out or args.out.with_suffix(".md")
        markdown = render_relocation_plan(plan)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown, encoding="utf-8")
        move_count = sum(item.action == "move-and-patch" for item in plan.items)
        print(
            f"Planned {move_count} move(s); wrote {args.out} and {markdown_path}. "
            "No files were changed."
        )
        return 0
    if args.command == "validate-plan":
        plan = read_relocation_plan(args.plan)
        result = validate_relocation_plan(plan)
        report = render_validation(result)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report, encoding="utf-8")
        print(report, end="")
        return 0 if result.is_valid else 1
    if args.command == "apply":
        plan = read_relocation_plan(args.plan)
        try:
            manifest, validation = apply_single_file(
                plan,
                only=args.only,
                manifest_path=args.manifest,
                p4=P4Client(dry_run=False),
            )
        except ApplyError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(render_validation(validation), end="")
        print(f"Applied one relocation; rollback manifest: {args.manifest}")
        return 0
    if args.command == "validate-apply":
        manifest = read_rollback_manifest(args.manifest)
        if manifest.operation_mode == "local-filesystem":
            result = validate_applied_filesystem_manifest(manifest)
        else:
            result = validate_applied_manifest(
                manifest, p4=P4Client(dry_run=False)
            )
        try:
            live_result = validate_live_wwise_manifest_at_url(
                manifest, url=args.waapi_url
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        result = type(result)(result.issues + live_result.issues)
        report = render_validation(result)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report, encoding="utf-8")
        print(report, end="")
        return 0 if result.is_valid else 1
    if args.command == "rollback":
        manifest = read_rollback_manifest(args.manifest)
        if manifest.operation_mode == "local-filesystem":
            result = rollback_local_manifest(
                manifest,
                manifest_path=args.manifest,
            )
        else:
            result = rollback_manifest(
                manifest,
                p4=P4Client(dry_run=False),
                manifest_path=args.manifest,
            )
        print(render_validation(result), end="")
        return 0 if result.is_valid else 1
    if args.command == "doctor":
        readiness = inspect_pilot_readiness(
            args.project_root,
            waapi_host=args.waapi_host,
            waapi_port=args.waapi_port,
        )
        if args.json_out:
            write_json_document(readiness, args.json_out)
        markdown = render_readiness_markdown(readiness)
        if args.markdown_out:
            args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_out.write_text(markdown, encoding="utf-8")
        print(markdown, end="")
        return 0 if readiness.ready else 1
    if args.command == "bootstrap-project":
        console = args.wwise_console or find_wwise_console()
        if console is None:
            print(
                "WwiseConsole was not found; pass its path with --wwise-console",
                file=sys.stderr,
            )
            return 1
        try:
            pilot = create_pilot_project(
                args.project_root,
                wwise_console=console,
                project_name=args.project_name,
                platform=args.platform,
                language=args.language,
                object_root=args.object_root,
                category=args.category,
                chapter=args.chapter,
                source_category=args.source_category,
                sound_name=args.sound_name,
            )
        except PilotProjectError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(
            f"Created populated Wwise pilot project: {pilot.project_file}\n"
            f"Object root: {pilot.object_root}\n"
            f"Fixture items: {len(pilot.items)}"
        )
        for item in pilot.items:
            print(
                f"- {item.category}: {item.source_relative_path}"
                f" -> {item.target_relative_path}"
            )
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .planner import build_noop_plan
from .report import write_json_plan, write_markdown_plan
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inspect-wwu":
        references = parse_source_references(
            args.wwu, project_root=args.project_root
        )
        plan = build_noop_plan(args.project_root, references)
        write_json_plan(plan, args.json_out)
        write_markdown_plan(plan, args.markdown_out)
        print(
            f"Discovered {len(references)} source reference(s); "
            "wrote a no-op plan and performed no mutations."
        )
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")

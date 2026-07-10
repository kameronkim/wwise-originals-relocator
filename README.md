# Wwise P4 Source Relocator

A Perforce-aware Wwise Originals relocation tool.

This tool helps reorganize Wwise Originals WAV files without breaking Wwise
source references or losing Perforce file history. It builds a relocation plan,
moves WAV files through `p4 move`, patches Wwise `.wwu` source references, and
validates that affected Wwise objects keep their GUIDs and do not become missing
sources.

It is designed for source cleanup tasks such as splitting a mixed
`Scenario/CH04` voice folder into `Cutscene/CH04` and `Script/CH04` while
preserving both Wwise project integrity and Perforce history.

## Current status

The initial source-inspection foundation is implemented as a safe, read-only
workflow:

- parse `AudioFileSource` references from a `.wwu` file;
- emit a no-op JSON plan and Markdown report;
- construct, but do not execute, `p4` commands in dry-run mode;
- exercise the behavior against a small fixture project.

No file move, `.wwu` patch, changelist submission, or Wwise import behavior is
implemented yet.

## Requirements

- Python 3.11 or newer
- `pytest` to run the tests (development only)

The current runtime uses only the Python standard library. Later features will
add the dependencies needed for WAAPI access, validated models, and console
output.

## Try the source inspector

From the repository root:

```bash
PYTHONPATH=src python -m wwise_p4_source_relocator inspect-wwu \
  --wwu "tests/fixtures/sample_project/Actor-Mixer Hierarchy/Default Work Unit.wwu" \
  --project-root tests/fixtures/sample_project \
  --json-out reports/source-plan.json \
  --markdown-out reports/source-plan.md
```

The generated plan is intentionally no-op: every discovered source is marked
`skip` with an inspection-only reason. This establishes source discovery and
report formats without implying that relocation is safe to apply.

Run the tests with:

```bash
python -m pytest
```

## Safety contract

- WAV relocation will only use `p4 move`.
- The tool will never submit a changelist.
- Exact source-path matching is required before patching a `.wwu`.
- Ambiguous or shared sources must stop automation and require review.
- Batch apply will not be implemented before a single-file pilot is validated.
- Rollback manifests are mandatory for every future apply operation.

See [the development specification](docs/development-spec.md) for the planned
milestones and complete design.

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

The current implementation provides a safe, read-only planning workflow:

- parse `AudioFileSource` references from a `.wwu` file;
- scan Wwise Sound and Audio File Source objects through WAAPI;
- classify `Cutscene`, `Script`, `Dialog`, and `Dynamic` tree categories;
- generate JSON and Markdown relocation plans;
- reject missing, multiple, shared, or ambiguous sources;
- preflight filesystem and Perforce workspace state;
- construct, but do not execute, `p4` commands in dry-run mode;
- exercise the behavior against a small fixture project.

No file move, `.wwu` patch, changelist submission, or Wwise import behavior is
implemented. Planning commands do not change project files.

## Requirements

- Python 3.11 or newer
- `pytest` to run the tests (development only)
- `waapi-client` for live Wwise scanning

The core parser and planner use only the Python standard library. Live scanning
adds `waapi-client` as an optional dependency.

Install the live-scanning extra with:

```bash
python -m pip install -e ".[waapi]"
```

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

## Scan and build a relocation plan

With Wwise running and WAAPI enabled:

```bash
wwise-p4-source-relocator scan \
  --project-root "D:\Work\Dev\Ilias\Ilias_WwiseProject" \
  --object-root "\\Containers\\Default Work Unit\\VO\\Temp_VO" \
  --chapter CH04 \
  --out reports/ch04-scan.json

wwise-p4-source-relocator plan \
  --scan reports/ch04-scan.json \
  --out reports/ch04-plan.json

wwise-p4-source-relocator validate-plan \
  --plan reports/ch04-plan.json \
  --report reports/ch04-validation.md
```

`validate-plan` exits with a non-zero status when a manual-review item or hard
preflight error is present. It requires `p4` to be installed and the affected
WAV and Work Unit paths to belong to the current Perforce workspace.

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

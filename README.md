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

The current implementation provides planning and guarded single-file pilot
execution:

- parse `AudioFileSource` references from a `.wwu` file;
- scan Wwise Sound and Audio File Source objects through WAAPI;
- classify `Cutscene`, `Script`, `Dialog`, and `Dynamic` tree categories;
- generate JSON and Markdown relocation plans;
- reject missing, multiple, shared, or ambiguous sources;
- preflight filesystem and Perforce workspace state;
- open the WWU and source WAV with `p4 edit`, then relocate through `p4 move`;
- patch one GUID-scoped, exact WWU source path without XML reformatting;
- write a rollback manifest before running any mutating Perforce command;
- validate filesystem, WWU hash, Perforce move state, and the WWU diff;
- validate Wwise GUID, object path, source path, and source existence via WAAPI;
- roll back only the paths listed in the manifest;
- exercise the behavior against a small fixture project.

Batch apply, changelist submission, and Wwise import as a relocation mechanism
are not implemented. The disposable-project bootstrap uses WwiseConsole import
only to create its isolated test fixture.
Planning commands remain read-only, and `apply` refuses to run unless `--only`
selects exactly one safe move candidate.

For a browser-ready Korean walkthrough of installation, fixture tests, the
disposable Wwise and Perforce pilot, live validation, and rollback, open the
[testing and usage guide](docs/usage-guide.html).

## Requirements

- Python 3.11 or newer
- `pytest` to run the tests (development only)
- `waapi-client` for live Wwise scanning and validation

The core parser, planner, and file patcher use only the Python standard library.
Live Wwise access adds `waapi-client` as an optional dependency.

Install the live-scanning extra with:

```bash
python -m pip install -e ".[waapi]"
```

## Create a disposable Wwise pilot project

On a machine with Wwise Authoring installed, create a populated project without
touching an existing `.wproj`:

```bash
PYTHONPATH=src python -m wwise_p4_source_relocator bootstrap-project \
  --project-root /private/tmp/wwise-relocator-p4/workspace/WwiseRelocatorPilot
```

The command refuses to use a non-empty destination. It invokes WwiseConsole to
create a new project and imports a generated PCM voice WAV into:

```text
Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav
```

The Wwise object is created under
`\Containers\Default Work Unit\VO\Script\CH04`, so the planner expects the WAV
to move from `Scenario/CH04` to `Script/CH04`. The project root also receives
`relocator-pilot.json` with the exact scan inputs and expected paths.

Run Wwise headlessly for live scanning and validation:

```bash
"/path/to/WwiseConsole.sh" waapi-server \
  /private/tmp/wwise-relocator-p4/workspace/WwiseRelocatorPilot/WwiseRelocatorPilot.wproj \
  --wamp-port 18080 \
  --http-port 18090 \
  --allowed-origin localhost,127.0.0.1
```

See [the live Wwise pilot](docs/live-wwise-pilot.md) for the complete Perforce,
WAAPI, apply, validation, and rollback sequence.

## Check pilot readiness

Before selecting a file, verify the local project and toolchain:

```bash
wwise-p4-source-relocator doctor \
  --project-root "D:\Work\Dev\Ilias\Ilias_WwiseProject" \
  --json-out reports/pilot-readiness.json \
  --markdown-out reports/pilot-readiness.md
```

The command checks for one Wwise project file, Originals WAV files, WWU source
references, the `p4` CLI and workspace mapping, `waapi-client`, and a reachable
WAAPI server. It performs no project or Perforce mutations.

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

## Run a single-file pilot

After reviewing a valid plan, select exactly one WAV:

```bash
wwise-p4-source-relocator apply \
  --plan reports/ch04-plan.json \
  --only CH04_S102_WT_001.wav \
  --changelist 123456 \
  --manifest reports/pilot-manifest.json
```

The manifest is written before `p4 edit` or `p4 move`. If the WWU patch or local
post-apply checks fail, the tool immediately attempts to revert only the moved
WAV and edited Work Unit recorded in that manifest.

Wwise must reload the externally changed Work Unit before live validation. In
Wwise, accept the External Project Changes prompt and reload the affected Work
Unit, then run:

```bash
wwise-p4-source-relocator validate-apply \
  --manifest reports/pilot-manifest.json \
  --report reports/pilot-validation.md
```

To restore the pilot without submitting anything:

```bash
wwise-p4-source-relocator rollback \
  --manifest reports/pilot-manifest.json
```

`rollback` never issues a broad `p4 revert //...`; it uses only the exact paths
recorded in the manifest.

If no shared Perforce environment is available, follow the
[local disposable Perforce pilot](docs/local-perforce-pilot.md) to validate the
real move and rollback behavior without touching a production depot.

Run the tests with:

```bash
python -m pytest
```

## Safety contract

- WAV relocation will only use `p4 move`.
- The tool will never submit a changelist.
- Exact source-path matching is required before patching a `.wwu`.
- Ambiguous or shared sources must stop automation and require review.
- Batch apply is unavailable until a real single-file pilot is validated.
- Rollback manifests are mandatory for every apply operation.

See [the development specification](docs/development-spec.md) for the planned
milestones and complete design.

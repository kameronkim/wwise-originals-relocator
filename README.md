# Wwise Originals Relocator

A portable desktop tool for moving Wwise Originals WAV files while preserving
Perforce history and Wwise source references.

The application builds a relocation plan, moves approved WAV files, patches
only the matching Wwise Work Unit source paths, and validates the result against
the filesystem and live Wwise objects. Normal operations use `p4 move` and add
Perforce validation before the final P4V review.

## Download

The current build is the
[v0.1.0-rc.8 pre-release](https://github.com/kameronkim/wwise-originals-relocator/releases/tag/v0.1.0-rc.8):

- Windows x64 portable ZIP
- macOS arm64 portable ZIP
- SHA-256 checksums

Extract the complete ZIP to a writable local folder. Keep the executable,
`_internal` folder, bundled offline HTML guide, `LICENSE.txt`, and
`VERSION.txt` together. The portable app does not install Python or other
dependencies.

## Requirements

- Wwise Authoring with WAAPI enabled and the target project open
- Perforce CLI (`p4` or `p4.exe`) and a mapped workspace for Perforce-tracked
  operations; neither is required for local test mode
- P4V for the final diff review and submit or revert workflow in normal mode
- Windows x64 or macOS arm64

The application does not install Wwise, Perforce, WebView2, or system web
components.

WAAPI connections are limited to `localhost` and loopback IP addresses. Wwise
Authoring and the portable app must run on the same computer.

The GUI reads the effective non-secret Perforce settings reported by `p4 set`.
If `P4CLIENT` is missing, it checks the current user's workspaces on the current
host and selects one only when exactly one workspace maps the chosen Wwise
project. Ambiguous or unmapped projects remain blocked for explicit review.

Launching the app from a P4V custom tool is still the most reliable option:
P4V passes the active `P4PORT`, `P4USER`, `P4CLIENT`, and `P4CHARSET` context
directly to the app. The existing Perforce login ticket is reused; passwords
and tickets are never stored by the application.

## Perforce operator workflow

1. Open the target project in Wwise and enable WAAPI.
2. Start the portable application and select the folder containing one
   `.wproj` file.
3. Run the environment check.
4. Build and review the relocation plan.
5. Select one or more safe items and confirm the complete path list.
6. Apply the move and wait for the **Wwise Reload required** state.
7. Reload the affected Work Unit in Wwise External Project Changes, then run
   validation.
8. Hand the validated change to P4V, or roll it back with the recorded manifest.
   Complete Apply, Wwise reload, validation, and any Rollback from the same
   extracted release folder; do not replace or move that folder while an
   operation remains open.

The Korean [offline usage guide](docs/usage-guide.html) contains the complete
screen-based instructions and troubleshooting steps. The same guide is bundled
inside every portable ZIP.

## Perforce-free test mode

Enable the **Perforce-free local test mode** to exercise Wwise/WAAPI scanning,
planning, and the complete local file-change cycle without a Perforce
installation. Apply moves the selected WAV files directly on disk, patches the
matching Work Unit source paths, and waits for the same Wwise External Project
Changes reload used by a normal operation. After reload, the app validates the
local files, Work Unit references, and live Wwise objects. A manifest is saved
before mutation so the same portable app folder can safely roll the test back.

Local test mode does not create Perforce `move/add` or `move/delete` metadata
and does not offer P4V handoff. Treat it as a Wwise and filesystem rehearsal:
roll it back, disable local test mode, and start a new normal operation when the
change must be tracked and reviewed in Perforce.

## Safety boundaries

- Planning and readiness checks do not modify the project.
- Normal WAV relocation uses `p4 move`; local test mode uses a direct filesystem
  move. The application never submits Perforce changes.
- Normal post-apply validation checks every expected `move/add`, `move/delete`,
  and Work Unit `edit`, and verifies that each source and target form one move
  pair. Local test validation checks the moved files, patched Work Units, and
  live Wwise objects without claiming Perforce state.
- A rollback manifest is saved before the first file mutation in either mode.
- Shared, ambiguous, missing, conflicting, or out-of-workspace sources stop
  automatic mutation.
- Existing local changes in an affected Work Unit stop a normal operation
  before the first Perforce mutation.
- In normal mode, read-only Perforce mapping and opened-state checks are grouped
  into bounded batches. Local Work Unit diffs are cached and checked
  individually, and each WAV move remains individually recorded and
  recoverable.
- A selected-file batch is fully preflighted before mutation and reverses
  completed moves if a later item fails.
- Wwise External Project Changes must be reloaded manually before live
  validation.
- Apply, validation, and Rollback must use the same extracted release folder so
  the operation manifest and reports remain available. Normal operations keep
  that folder until P4V submit or revert completes the work; local tests keep it
  until Rollback restores the project.

Every relocation plan and post-apply validation writes `performance.json`
beside its other reports. Plan reports record WAAPI, planning, preflight,
report-writing, and Perforce timings. Apply validation reports record local and
live Wwise validation durations plus the number of batched WAAPI requests.

The application acts only on the selected WAV files and affected Work Units.
Existing files already open in the workspace are not included in its validation,
submission, or rollback scope.

`v0.1.0-rc.8` remains a pre-release. A representative 332-file production
Apply, Wwise reload/validation, and P4V handoff passed, and a separate real
disposable `p4d` run restored a two-file batch and its shared Work Unit. The
post-RC8 cleanup still needs an exact-candidate CI/build and frozen-WAMP-worker
smoke before the metadata-only final `v0.1.0` promotion.

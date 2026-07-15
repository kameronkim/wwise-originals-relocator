# Wwise Originals Relocator

A portable desktop tool for moving Wwise Originals WAV files while preserving
Perforce history and Wwise source references.

The application builds a relocation plan, moves approved WAV files with
`p4 move`, patches only the matching Wwise Work Unit source paths, and validates
the result against the filesystem, Perforce, and live Wwise objects.

## Download

The current build is the
[v0.1.0-rc.6 pre-release](https://github.com/kameronkim/wwise-originals-relocator/releases/tag/v0.1.0-rc.6):

- Windows x64 portable ZIP
- macOS arm64 portable ZIP
- SHA-256 checksums

Extract the complete ZIP to a writable local folder. Keep the executable,
`_internal` folder, bundled offline HTML guide, `LICENSE.txt`, and
`VERSION.txt` together. The portable app does not install Python or other
dependencies.

## Requirements

- Wwise Authoring with WAAPI enabled and the target project open
- Perforce CLI (`p4` or `p4.exe`) and a mapped workspace for real operations
- P4V for the final diff review and submit or revert workflow
- Windows x64 or a macOS build matching the target CPU architecture

The application does not install Wwise, Perforce, WebView2, or system web
components.

The GUI reads the effective non-secret Perforce settings reported by `p4 set`.
If `P4CLIENT` is missing, it checks the current user's workspaces on the current
host and selects one only when exactly one workspace maps the chosen Wwise
project. Ambiguous or unmapped projects remain blocked for explicit review.

Launching the app from a P4V custom tool is still the most reliable option:
P4V passes the active `P4PORT`, `P4USER`, `P4CLIENT`, and `P4CHARSET` context
directly to the app. The existing Perforce login ticket is reused; passwords
and tickets are never stored by the application.

## Operator workflow

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

The Korean [offline usage guide](docs/usage-guide.html) contains the complete
screen-based instructions and troubleshooting steps. The same guide is bundled
inside every portable ZIP.

## Perforce-free test mode

Enable the **Perforce-free local test mode** to exercise Wwise/WAAPI scanning,
local path checks, planning, and reports without a Perforce installation.
Mutation, apply, and rollback controls remain disabled in this mode.

## Safety boundaries

- Planning and readiness checks do not modify the project.
- WAV relocation uses `p4 move`; the application never submits Perforce changes.
- Post-apply validation checks every expected `move/add`, `move/delete`, and
  Work Unit `edit`, and verifies that each source and target form one move pair.
- A rollback manifest is saved before the first mutating Perforce command.
- Shared, ambiguous, missing, conflicting, or out-of-workspace sources stop
  automatic mutation.
- Existing local changes in an affected Work Unit stop the operation before
  the first Perforce mutation.
- Read-only Perforce mapping and opened-state checks are grouped into bounded
  batches. Local Work Unit diffs are cached and checked individually, and each
  WAV move remains individually recorded and recoverable.
- A selected-file batch is fully preflighted before mutation and reverses
  completed moves if a later item fails.
- Wwise External Project Changes must be reloaded manually before live
  validation.

Every relocation plan and post-apply validation writes `performance.json`
beside its other reports. Plan reports record WAAPI, planning, preflight,
report-writing, and Perforce timings. Apply validation reports record local and
live Wwise validation durations plus the number of batched WAAPI requests.

The application acts only on the selected WAV files and affected Work Units.
Existing files already open in the workspace are not included in its validation,
submission, or rollback scope.

`v0.1.0-rc.6` remains a pre-release because a real multi-file Wwise and
Perforce apply/validate/rollback pilot is still required before final
`v0.1.0` approval.

# Wwise Originals Relocator

A portable desktop tool for moving Wwise Originals WAV files while preserving
Perforce history and Wwise source references.

The application builds a relocation plan, moves approved WAV files with
`p4 move`, patches only the matching Wwise Work Unit source paths, and validates
the result against the filesystem, Perforce, and live Wwise objects.

## Download

The current build is the
[v0.1.0-rc.2 pre-release](https://github.com/kameronkim/wwise-originals-relocator/releases/tag/v0.1.0-rc.2):

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

When launched from a P4V custom tool, the GUI imports the active `P4PORT`,
`P4USER`, `P4CLIENT`, and `P4CHARSET` context. It reuses the existing Perforce
login ticket and never stores a password or ticket.

## Operator workflow

1. Open the target project in Wwise and enable WAAPI.
2. Start the portable application and select the folder containing one
   `.wproj` file.
3. Run the environment check.
4. Build and review the relocation plan.
5. Select one or more safe items and confirm the complete path list.
6. Apply the move, reload External Project Changes in Wwise, and run validation.
7. Hand the validated change to P4V, or roll it back with the recorded manifest.

The Korean [offline usage guide](docs/usage-guide.html) contains the complete
screen-based instructions and troubleshooting steps. The same guide is bundled
inside every portable ZIP.

## Perforce-free test mode

Enable the **Perforce-free local test mode** to exercise Wwise/WAAPI scanning,
local path checks, planning, and reports without a Perforce installation.
Mutation, apply, and rollback controls remain disabled in this mode.

## Safety boundaries

- Planning and readiness checks do not modify the project.
- WAV relocation uses `p4 move`; the application never submits a changelist.
- A rollback manifest is saved before the first mutating Perforce command.
- Shared, ambiguous, missing, conflicting, or out-of-workspace sources stop
  automatic mutation.
- A selected-file batch is fully preflighted before mutation and reverses
  completed moves if a later item fails.
- Wwise External Project Changes must be reloaded manually before live
  validation.

`v0.1.0-rc.2` remains a pre-release because a real multi-file Wwise and
Perforce apply/validate/rollback pilot is still required before final
`v0.1.0` approval.

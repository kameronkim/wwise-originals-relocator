# Changelog

All notable user-visible changes are recorded in this file. This project uses
semantic version tags after the release gates in `RELEASING.md` pass.

## Unreleased

Target: `v0.1.0`

### Added

- Portable desktop GUI for Windows x64 and macOS one-folder distributions.
- Wwise project readiness checks, automatic WAAPI endpoint detection, source
  scanning, relocation planning, and offline reports.
- Explicit local test mode for Wwise and filesystem validation without
  Perforce mutation.
- Manifest-first single-file CLI and selected-file GUI apply operations using
  `p4 edit` and `p4 move`.
- Post-apply Wwise, filesystem, WWU, and Perforce validation with exact-manifest
  rollback.
- P4V handoff, closeout validation, and recent-operation history.
- Disposable Wwise project and local Helix Core pilot procedures.

### Safety

- The application never submits a changelist, installs Wwise or Perforce, or
  reloads Wwise project changes automatically.
- Shared, ambiguous, missing, conflicting, or out-of-workspace sources stop
  automated mutation.
- A selected-file batch is fully preflighted before mutation and reverses
  completed moves if a later item fails.

### Release gate

- Complete and record one real multi-file Wwise and Perforce
  apply/validate/rollback pilot before moving these notes to `0.1.0`, creating
  the `v0.1.0` tag, or publishing portable artifacts.

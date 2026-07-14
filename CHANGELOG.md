# Changelog

All notable user-visible changes are recorded in this file. Final semantic
version tags are created only after the required validation passes. A
pre-release may document an outstanding live-validation gate.

## Unreleased

Target: `v0.1.0`

### Release gate

- Complete and record one real multi-file Wwise and Perforce
  apply/validate/rollback pilot before creating the final `v0.1.0` tag.

## [0.1.0-rc.2] - 2026-07-14

### Added

- P4V connection import for server, user, workspace, and charset settings.
- Explicit Perforce connection validation and project mapping diagnostics.
- A redesigned offline usage guide with responsive navigation, status cards,
  workflow diagrams, theme controls, and screen-focused troubleshooting.

### Fixed

- Prevented pywebview from recursively inspecting the native Windows window,
  which could stall the JavaScript bridge and grow the log continuously.
- Limited portable GUI logs with rotation to prevent unbounded log growth.

### Validation status

- Automated P4V connection, GUI service, readiness, portable packaging, and
  usage-guide checks pass on the release source.
- A real multi-file Wwise and Perforce pilot remains outstanding; this build is
  a release candidate and is not the final `v0.1.0` release.

## [0.1.0-rc.1] - 2026-07-14

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

### Validation status

- Automated selected-file batch tests, portable smoke tests, a live single-file
  Wwise validation, and a disposable local Helix Core apply/rollback pilot are
  complete.
- A real multi-file Wwise and Perforce pilot remains outstanding; this build is
  a release candidate and is not the final `v0.1.0` release.

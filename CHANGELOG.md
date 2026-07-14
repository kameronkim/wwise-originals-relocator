# Changelog

All notable user-visible changes are recorded in this file. Final semantic
version tags are created only after the required validation passes. A
pre-release may document an outstanding live-validation gate.

## Unreleased

Target: `v0.1.0`

### Release gate

- Complete and record one real multi-file Wwise and Perforce
  apply/validate/rollback pilot before creating the final `v0.1.0` tag.

## [0.1.0-rc.3] - 2026-07-14

### Added

- A read-only Windows PowerShell diagnostic that compares the existing
  Perforce context with the connection replayed by the portable application.
- Project-aware workspace discovery when `P4CLIENT` is not already available.

### Fixed

- Preserved the effective `P4PORT` reported by local Perforce settings instead
  of replacing it with the server's internal `serverAddress`.
- Recovered from stale connection settings saved by an earlier portable build.
- Distinguished server connection failures, missing workspace selection, and
  projects outside the selected workspace in the GUI and readiness report.
- Reused the resolved workspace for the final `p4 where` mapping check.

### Validation status

- 106 automated tests and 8 subtests pass on the release source.
- The supplied Windows diagnostic reproduced and confirmed the original
  `localhost.localdomain:1666` connection failure before this fix.
- A real multi-file Wwise and Perforce pilot remains outstanding; this build is
  a release candidate and is not the final `v0.1.0` release.

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

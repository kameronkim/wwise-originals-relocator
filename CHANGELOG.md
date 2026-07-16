# Changelog

All notable user-visible changes are recorded in this file. Final semantic
version tags are created only after the required validation passes. A
pre-release may document an outstanding live-validation gate.

## Unreleased

### Fixed

- Redact non-public Perforce settings from diagnostic logs when `p4 set`
  fails.
- Correct RC8 download, rollback-scope, and platform guidance across the public
  README and offline usage guide.

### Changed

- Run the portable validation workflow for pull requests targeting `develop`
  or `main`, including dependency update pull requests.
- Publish the exact Windows portable ZIP produced by the build as the workflow
  artifact.
- Expand the real disposable Perforce integration to a two-file batch Apply
  and exact manifest Rollback with restored WAV, Work Unit, and opened-state
  checks. Local verification also covers P4 Server 2026.1 secure first-user
  bootstrap; Windows CI remains pinned to the reviewed 2025.1 binaries.

### Validation status

- RC8 completed a representative 332-file production Apply, two successful
  Wwise validations, P4V handoff, and final closeout.
- A separate loopback-only real `p4d` run completed a two-file Apply/Rollback
  cycle and restored both WAV hashes, the shared Work Unit hash, and a clean
  project `p4 opened` state.

## [0.1.0-rc.8] - 2026-07-16

### Fixed

- Bound blocking WAMP apply validation to 90 seconds so an unresponsive Wwise
  connection cannot hold the GUI operation lock indefinitely.
- Bypass environment and system HTTP proxies for same-machine WAAPI requests.
- Report malformed or out-of-range WAAPI ports as actionable connection errors.

### Changed

- Standardized GitHub release titles as version-only `v<version>` and
  portable archive names as `WwiseOriginalsRelocator-<os>-<arch>.zip`.
- Made the macOS build script include its detected `arm64` or `x64`
  architecture in the generated ZIP name.
- Audited the complete portable dependency set before building the Windows
  executable and raised the Bandit gate to medium severity and above.

### Security

- Reject XML entity declarations while reading Wwise Work Units.
- Restrict WAAPI connections to loopback addresses used by the local Wwise
  Authoring process and force direct connections that do not use HTTP proxies.
- Pin GitHub Actions and release tool versions, verify downloaded Perforce test
  binaries by SHA-256, and run dependency plus static security audits in CI.

### Release gate

- Complete and record one real multi-file Wwise and Perforce
  apply/validate/rollback pilot before creating the final `v0.1.0` tag.

## [0.1.0-rc.7] - 2026-07-15

### Fixed

- Run post-apply `p4 fstat` with explicit tagged output so successful
  `move/add`, `move/delete`, and Work Unit `edit` records are parsed instead of
  being incorrectly reported as missing and automatically rolled back.
- Let the macOS build script reuse or create its local virtual environment and
  discover `python3` when the legacy `python` command is unavailable.

### Changed

- Added a disposable Windows Perforce 2025.1 server test that exercises the
  production `fstat` command, a real move pair, Work Unit edit, complete Apply
  transition, and manifest-scoped rollback.
- Removed temporary standalone Perforce diagnostic scripts and the unused
  repository-level reports placeholder from the release source.

### Validation status

- 135 automated tests and 8 subtests pass on the release source.
- The Windows Actions integration creates a real disposable `p4d`, validates
  three structured opened records, reaches `awaiting-wwise-reload`, and rolls
  the operation back successfully.
- The macOS arm64 portable executable passes its packaged smoke check.
- A real multi-file Wwise and Perforce pilot remains outstanding; this build is
  a release candidate and is not the final `v0.1.0` release.

## [0.1.0-rc.6] - 2026-07-15

### Changed

- Removed manual numbered changelist setup from the GUI and CLI. Relocations
  now operate on the selected paths in the current Perforce workspace state.
- Kept post-apply validation focused on each selected path's action and linked
  move pair without rejecting unrelated files already open in the workspace.

### Fixed

- Match Windows Perforce records through both client-syntax and local path
  aliases during post-apply validation.
- Report successful automatic rollback and refresh operation history after an
  apply validation failure.

### Validation status

- 133 automated tests and 8 subtests pass.
- Regression scenarios cover unrelated opened files, legacy saved settings,
  Windows client/local path aliases, and completed automatic rollback history.
- A real multi-file Wwise and Perforce pilot remains outstanding; this build is
  a release candidate and is not the final `v0.1.0` release.

## [0.1.0-rc.5] - 2026-07-15

### Added

- Structured post-apply Perforce validation for every expected `move/add`,
  `move/delete`, and Work Unit `edit` action.
- Move-pair, changelist-assignment, missing-file, and unexpected-file checks
  with an inline GUI summary and a detailed validation report.

### Changed

- Recommended a dedicated numbered changelist in the Apply workflow because
  exact-scope validation rejects unrelated files in the same changelist.

### Validation status

- 131 automated tests and 8 subtests pass.
- Structured fake-Perforce scenarios cover correct numbered and default
  changelists, wrong actions, broken move pairs, files in another changelist,
  and unrelated files in the operation changelist.
- The macOS arm64 portable build passes its executable smoke check.
- A real multi-file Wwise and Perforce pilot remains outstanding; this build is
  a release candidate and is not the final `v0.1.0` release.

## [0.1.0-rc.4] - 2026-07-15

### Added

- Bulk selection and compact, scrollable review for large relocation plans.
- Explicit Wwise Reload waiting state before live apply validation.
- Automatic discovery of a unique Wwise Object Root below the configured path.
- Per-plan `performance.json` reports with stage timings and Perforce command
  metrics.
- Post-apply `performance.json` reports with local validation, live Wwise,
  total duration, batch size, and WAAPI request count.
- A local HTTP WAAPI server test covering 100 objects, reordered responses,
  missing and duplicate objects, and server errors.

### Changed

- Grouped read-only `p4 where` and `p4 opened` checks into bounded batches while
  keeping Work Unit diffs and WAV moves individually verifiable.
- Added an inline plan summary for WAAPI, Perforce, and table-render timing.
- Grouped live Wwise object validation into batches of 32 and matched replies
  by GUID or object path instead of response order.

### Fixed

- Blocked apply when an affected Work Unit already has unrelated local changes.
- Added actionable rollback failure reports and hash mismatch details.
- Prevented Perforce subprocess windows from flashing during Windows checks.

### Validation status

- 123 automated tests and 8 subtests pass.
- The 100-item browser preview renders without horizontal overflow and keeps
  the plan table in its bounded scroll region.
- A synthetic 100-item, single-Work-Unit preflight uses 12 read-only Perforce
  calls with the configured batch size of 32 paths.
- A 100-object virtual HTTP WAAPI validation completes in 4 requests and
  confirms order-independent matching and actionable error reporting.

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

# Wwise Originals Relocator

A portable desktop tool that aligns Wwise Originals WAV folders with the
logical organization of a Wwise project. It creates a reviewable relocation
plan, moves only approved files, updates the matching Work Unit source
references, and validates the result in Wwise.

Perforce is optional. In a mapped Perforce workspace, the application uses
`p4 move` so file history can continue when the user reviews and submits the
change in P4V. Without Perforce, local test mode provides the same move, Wwise
reload, validation, and rollback cycle directly on the filesystem.

## Download

The current stable version is the
[v0.1.0 release](https://github.com/kameronkim/wwise-originals-relocator/releases/tag/v0.1.0):

- Windows x64 portable ZIP
- macOS arm64 portable ZIP
- SHA-256 checksums

Extract the complete ZIP to a writable local folder. Keep the executable,
`_internal` folder, bundled guide, `LICENSE.txt`, and `VERSION.txt` together.
The app does not install Python or other dependencies.

The current binaries are not publisher-signed or notarized. Verify the
provided checksum and follow the operating system approval steps in the
[usage guide](docs/usage-guide.html) when prompted.

## What it does

- Reads Wwise Sound objects and their source references through local WAAPI.
- Maps supported voice categories to
  `Originals/Voices/<Language>/<Category>/<Chapter>/<File>`.
- Moves selected WAV files and patches only the matching Work Units.
- Validates local files, Wwise references, and Perforce move pairs when used.
- Records a rollback manifest before changing any project file.

The current mapping profile supports `Cutscene`, `Script`, `Dialog`, and
`Dynamic` categories. Shared, ambiguous, conflicting, or already-correct
sources are skipped or held for review.

## Requirements

- Wwise Authoring with WAAPI enabled and the target project open
- Windows x64 or macOS arm64
- Perforce CLI and a mapped workspace for history-preserving moves
- P4V for final review, submit, or revert in Perforce mode

Perforce and P4V are not required for local test mode. Wwise, Perforce,
WebView2, and system components are not installed or bundled by the app.

## Quick start

1. Open the target project in Wwise and enable WAAPI.
2. Start the portable app and select the folder containing one `.wproj` file.
3. Run the environment check, then create and review the relocation plan.
4. Select the safe items, apply them, and reload the affected Work Unit when
   Wwise reports external project changes.
5. Run validation, then hand the result to P4V or use the recorded rollback.

Complete the operation from the same extracted release folder. Do not move or
replace that folder until Apply, Wwise reload, validation, and any Rollback are
finished.

## Safety

- Planning and environment checks do not modify the project.
- The application never overwrites an existing destination or submits a
  Perforce changelist.
- A manifest is written before mutation, and incomplete batches are reversed.
- WAAPI connections are restricted to the local machine; Perforce credentials
  and tickets are not stored.
- Local test mode performs no Perforce operations and provides full rollback.

See the Korean [portable usage guide](docs/usage-guide.html) for complete
screen-based instructions, troubleshooting, and operating-system approval
steps. Release-specific validation evidence is recorded in
[CHANGELOG.md](CHANGELOG.md) and the corresponding GitHub Release.

## License

[MIT](LICENSE)

#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_root="${1:-portable-dist}"

cd "$repo_root"
python -m pip install ".[portable]"
python -m PyInstaller --noconfirm --clean packaging/wwise-relocator.spec
python scripts/prepare_portable.py --app-root dist/WwiseOriginalsRelocator
mkdir -p "$output_root"

archive="$output_root/WwiseOriginalsRelocator-macos.zip"
rm -f "$archive"
(cd dist/WwiseOriginalsRelocator && zip -qr "$repo_root/$archive" .)
echo "Portable archive: $repo_root/$archive"

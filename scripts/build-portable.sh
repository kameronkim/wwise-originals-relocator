#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_root="${1:-portable-dist}"

if [[ -n "${PYTHON:-}" ]]; then
    python_bin="$PYTHON"
elif [[ -x "$repo_root/.venv/bin/python" ]]; then
    python_bin="$repo_root/.venv/bin/python"
else
    if command -v python3 >/dev/null 2>&1; then
        bootstrap_python="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        bootstrap_python="$(command -v python)"
    else
        echo "Python 3.11 or newer is required to build the portable app." >&2
        exit 1
    fi
    "$bootstrap_python" -m venv "$repo_root/.venv"
    python_bin="$repo_root/.venv/bin/python"
fi

cd "$repo_root"
"$python_bin" -m pip install ".[portable]"
"$python_bin" -m PyInstaller --noconfirm --clean packaging/wwise-relocator.spec
"$python_bin" scripts/prepare_portable.py --app-root dist/WwiseOriginalsRelocator
mkdir -p "$output_root"

archive="$output_root/WwiseOriginalsRelocator-macos.zip"
rm -f "$archive"
(cd dist/WwiseOriginalsRelocator && zip -qr "$repo_root/$archive" .)
echo "Portable archive: $repo_root/$archive"

param(
    [string]$OutputRoot = "portable-dist",
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$DistRoot = Join-Path $RepoRoot "dist"
$AppRoot = Join-Path $DistRoot "WwiseOriginalsRelocator"
$ResolvedOutputRoot = Join-Path $RepoRoot $OutputRoot
$ArchivePath = Join-Path $ResolvedOutputRoot "WwiseOriginalsRelocator-windows-x64.zip"

Push-Location $RepoRoot
try {
    if (-not $SkipDependencyInstall) {
        python -m pip install ".[portable]"
    }

    python -m PyInstaller --noconfirm --clean "packaging/wwise-relocator.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }

    Copy-Item "docs/portable-gui.md" (Join-Path $AppRoot "사용가이드.md") -Force
    New-Item -ItemType Directory -Path $ResolvedOutputRoot -Force | Out-Null
    if (Test-Path $ArchivePath) {
        Remove-Item $ArchivePath -Force
    }
    Compress-Archive -Path (Join-Path $AppRoot "*") -DestinationPath $ArchivePath
    Write-Host "Portable archive: $ArchivePath"
}
finally {
    Pop-Location
}

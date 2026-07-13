from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


repo_root = Path(SPECPATH).parent
source_root = repo_root / "src"
gui_asset_root = (
    source_root / "wwise_p4_source_relocator" / "gui" / "assets"
)

datas = [
    (
        str(gui_asset_root),
        "wwise_p4_source_relocator/gui/assets",
    ),
]
datas += collect_data_files("webview")
hidden_imports = collect_submodules("waapi")

a = Analysis(
    [str(repo_root / "packaging" / "portable_entry.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WwiseOriginalsRelocator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WwiseOriginalsRelocator",
)

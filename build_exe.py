"""Build MMD-Trader as a standalone onefile .exe (Option B).

Usage (Windows):  build_exe.bat   (or:  pyinstaller build_exe.spec)
L'utilisateur lance MMD-Trader.exe : au 1er demarrage, l'assistant demande
CLIENT_ID/SECRET (sa propre app CCP), ecrit .env dans %APPDATA%/MMD-Trader,
puis connect_eve() peupe l'app. Aucun secret n'est embarque dans l'exe.
"""
import argparse
import os
from pathlib import Path

import PyInstaller.__main__
from version import VERSION

HERE = os.path.dirname(os.path.abspath(__file__))


def _version_tuple(value: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in value.split(".")]
    return tuple((parts + [0, 0, 0, 0])[:4])


def _write_windows_version_info(executable_name: str) -> Path:
    """Create a PyInstaller VERSIONINFO file from the central public version."""
    publisher = os.environ.get("MMD_PUBLISHER", "MMD Trader Contributors")
    company = os.environ.get("MMD_COMPANY_NAME", publisher)
    version_tuple = _version_tuple(VERSION)
    version_text = ".".join(str(part) for part in version_tuple)
    build_dir = Path(HERE) / "build" / "version-info"
    build_dir.mkdir(parents=True, exist_ok=True)
    target = build_dir / "mmd_version_info.txt"
    target.write_text(f'''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={version_tuple}, prodvers={version_tuple}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', {company!r}),
      StringStruct('FileDescription', 'EVE Online Market & New Eden Intelligence Assistant'),
      StringStruct('FileVersion', {version_text!r}),
      StringStruct('InternalName', 'MMD-Trader'),
      StringStruct('OriginalFilename', {executable_name!r}),
      StringStruct('Publisher', {publisher!r}),
      StringStruct('ProductName', 'MMD Trader'),
      StringStruct('ProductVersion', {version_text!r}),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ]
)
''', encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Build MMD-Trader without executable packers.")
    parser.add_argument("--onedir", action="store_true", help="Build the Defender-comparison folder variant.")
    args = parser.parse_args()
    executable_name = "MMD-Trader.exe"
    version_file = _write_windows_version_info(executable_name)
    mode = "--onedir" if args.onedir else "--onefile"
    workpath = "--workpath=build/onedir" if args.onedir else "--workpath=build/onefile"

    PyInstaller.__main__.run([
        "mmd_gui.py",
        "--name=MMD-Trader",
        mode,
        "--windowed",
        "--noconfirm",
        "--clean",
        "--noupx",  # Never compress/pack the executable with UPX.
        workpath,
        f"--version-file={version_file}",
        # donnees embarquees (chemin relatif au dossier de l'exe, pas _MEI)
        f"--add-data=gui;gui",
        f"--add-data=reference;reference",
        f"--add-data=README.md;.",
        f"--add-data=.env.example;.env.example",
        # exclusions : SDE 239MB + caches
        "--exclude-module=tkinter",
        "--hidden-import=webview",
        "--hidden-import=mmd_core",
        "--hidden-import=eve_map_service",
        # Loaded lazily by the map API once optional live overlays are requested.
        # Keep it explicit: onefile analysis cannot see that dynamic import.
        "--hidden-import=eve_map_intel_service",
        "--hidden-import=eve_map_kill_stream",
        "--hidden-import=eve_local_analyzer",
        "--hidden-import=eve_map_intel_alert",
        "--hidden-import=mmd_sso",
        "--hidden-import=mmd_margin",
        "--hidden-import=mmd_stations",
        "--hidden-import=mmd_esi_orders",
        "--hidden-import=mmd_crypto",
        "--hidden-import=platform_state",
        "--hidden-import=repositories.character_repository",
        "--hidden-import=repositories.corporation_order_repository",
        "--hidden-import=migrations",
        "--hidden-import=database",
    ])

    output = "dist/MMD-Trader/MMD-Trader.exe" if args.onedir else "dist/MMD-Trader.exe"
    print(f"BUILD DONE -> {output}")


if __name__ == "__main__":
    main()

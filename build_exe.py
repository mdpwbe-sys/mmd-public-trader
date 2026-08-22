"""Build MMD-Trader as a standalone onefile .exe (Option B).

Usage (Windows):  build_exe.bat   (or:  pyinstaller build_exe.spec)
L'utilisateur lance MMD-Trader.exe : au 1er demarrage, l'assistant demande
CLIENT_ID/SECRET (sa propre app CCP), ecrit .env dans %APPDATA%/MMD-Trader,
puis connect_eve() peupe l'app. Aucun secret n'est embarque dans l'exe.
"""
import os
import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))

PyInstaller.__main__.run([
    "mmd_gui.py",
    "--name=MMD-Trader",
    "--onefile",
    "--windowed",
    "--noconfirm",
    "--clean",
    # donnees embarquees (chemin relatif au dossier de l'exe, pas _MEI)
    f"--add-data=gui;gui",
    f"--add-data=reference;reference",
    f"--add-data=README.md;.",
    f"--add-data=.env.example;.env.example",
    # exclusions : SDE 239MB + caches
    "--exclude-module=tkinter",
    "--hidden-import=webview",
    "--hidden-import=mmd_core",
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

print("BUILD DONE -> dist/MMD-Trader.exe")

@echo off
REM Build MMD-Trader.exe (onefile + Defender-comparison onedir). Lance depuis le dossier du projet.
cd /d %~dp0
python -m pip install --quiet pyinstaller pywebview cryptography psutil
python build_exe.py
python build_exe.py --onedir
if exist "dist\MMD-Trader.exe" (
  echo.
  echo ============================================================
  echo  BUILD OK : dist\MMD-Trader.exe
  echo  - Release onefile : dist\MMD-Trader.exe
  echo  - Variante Defender : dist\MMD-Trader\MMD-Trader.exe
  echo  - 1er lancement : assistant CLIENT_ID/SECRET (ta propre app CCP).
  echo  - Donnees persistantes dans %%APPDATA%%\MMD-Trader.
  echo ============================================================
) else (
  echo ECHEC du build. Voir la sortie ci-dessus.
)
pause

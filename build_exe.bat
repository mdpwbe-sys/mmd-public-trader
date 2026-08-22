@echo off
REM Build standalone MMD-Trader.exe (Option B, onefile). Lance depuis le dossier du projet.
cd /d %~dp0
python -m pip install --quiet pyinstaller pywebview cryptography psutil
python build_exe.py
if exist "dist\MMD-Trader.exe" (
  echo.
  echo ============================================================
  echo  BUILD OK : dist\MMD-Trader.exe
  echo  - Copie ce .exe ou partage-le (rien d'autre requis).
  echo  - 1er lancement : assistant CLIENT_ID/SECRET (ta propre app CCP).
  echo  - Donnees persistantes dans %%APPDATA%%\MMD-Trader.
  echo ============================================================
) else (
  echo ECHEC du build. Voir la sortie ci-dessus.
)
pause

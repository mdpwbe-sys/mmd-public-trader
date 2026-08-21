@echo off
REM ============================================================
REM  Mmd Trading - Pipeline complet (Memorie Vault)
REM  1) ouvre Mmd -> tu fais Import all + Import prices
REM  2) mmd_check.py  -> doublons + prix depasses (console)
REM  3) mmd_history.py -> alimente le vault Obsidian (alertes ventes a part)
REM ============================================================
cd /d "%~dp0"
python mmd_check.py
echo.
echo [=== generation historique + alertes vault ===]
python mmd_history.py
echo.
echo (Appuie sur une touche pour fermer)
pause >nul

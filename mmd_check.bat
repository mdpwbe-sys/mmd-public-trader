@echo off
REM ============================================================
REM  Mmd Dashboard - bilan ordres + prix depasses
REM  Lance Mmd, attends ton import, affiche le bilan
REM ============================================================
cd /d "%~dp0"
python mmd_check.py
echo.
echo (Appuie sur une touche pour fermer)
pause >nul

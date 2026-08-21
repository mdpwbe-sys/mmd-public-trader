@echo off
REM ============================================================
REM  Mmd - Scan des doublons inter-personnages
REM  Double-clique -> affiche le rapport + ecrit dup_orders.txt
REM ============================================================
cd /d "%~dp0"
python dup_scan.py
echo.
echo (Appuie sur une touche pour fermer)
pause >nul

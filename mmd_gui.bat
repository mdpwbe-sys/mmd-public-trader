@echo off
REM ============================================================
REM  Mmd Order Manager - GUI launcher
REM  Lance l'interface desktop (pywebview) branchee sur le backend.
REM  Au demarrage: Scan automatique de la base Mmd.
REM  Pour rafraichir: bouton "Scan / Refresh".
REM ============================================================
SET PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe
IF NOT EXIST "%PY%" SET PY=C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe
IF NOT EXIST "%PY%" SET PY=python
cd /d "%~dp0"
"%PY%" mmd_gui.py
pause

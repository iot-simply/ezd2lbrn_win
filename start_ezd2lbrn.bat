@echo off
REM Startet die Oberflaeche direkt mit Python (ohne .exe)
cd /d "%~dp0"
python -m pip show fonttools >nul 2>&1 || python -m pip install fonttools
python -m pip show pillow >nul 2>&1 || python -m pip install pillow
python ezd2lbrn.py

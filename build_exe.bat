@echo off
REM Erstellt ezd2lbrn.exe (eigenstaendig, kein Python auf Zielrechner noetig)
REM Voraussetzung: Python 3.9+ installiert (python.org, Haken bei "Add to PATH")
REM Das iot-simply-Logo ist im Programm eingebaut und wird auch als Programmsymbol verwendet.
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install fonttools pillow pyinstaller
python ezd2lbrn.py --icon-export ezd2lbrn_icon.png
python -m PyInstaller --onefile --noconsole --name ezd2lbrn --icon ezd2lbrn_icon.png --collect-submodules fontTools --hidden-import PIL.ImageDraw --hidden-import PIL.ImageTk ezd2lbrn.py
del ezd2lbrn_icon.png
echo.
echo Fertig: dist\ezd2lbrn.exe
pause

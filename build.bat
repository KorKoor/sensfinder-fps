@echo off
rem Genera dist\SensFinder.exe con PyInstaller
python -m pip install pyinstaller
python -m PyInstaller --onefile --noconsole --name SensFinder --collect-submodules tkinter sensfinder.py
echo.
echo Listo: dist\SensFinder.exe

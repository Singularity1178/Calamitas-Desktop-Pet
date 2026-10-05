@echo off
REM Launch the Calamitas desktop pet. Press ESC to quit.
cd /d "%~dp0"
python -u calamitas_pet.py
if errorlevel 1 pause
@echo off
REM Launch the Calamitas desktop pet. Press ESC to quit.
REM It asks hooded / unhooded on startup; pass --variant hooded|unhooded to skip that.
cd /d "%~dp0"
python -u calamitas_pet.py %*
if errorlevel 1 pause
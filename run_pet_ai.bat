@echo off
REM ---------------------------------------------------------------------------
REM  Calamitas desktop pet -- PORTED AI version.
REM  Runs calamitas_pet_ai.py, whose fight logic is derived from the official
REM  Calamity Mod source. The file's own docstring carries the provenance notes
REM  and the per-constant citations.
REM
REM  Press ESC to quit.
REM
REM  The original hand-built pet is untouched: run_pet.bat still starts it.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
python -u calamitas_pet_ai.py %*
if errorlevel 1 pause

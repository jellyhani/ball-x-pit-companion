@echo off
rem Create the virtual environment with pinned dependencies (needs uv).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" uv venv .venv --python 3.13 || goto :fail
uv pip install --python .venv\Scripts\python.exe -r requirements.txt || goto :fail
echo Ready. Start with run_overlay.bat or play.bat.
pause
exit /b 0
:fail
echo Setup failed. Check that uv is installed: https://docs.astral.sh/uv/
pause
exit /b 1

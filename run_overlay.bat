@echo off
rem Start the BALL x PIT helper. Checks/installs the game bridge mod first.
rem ASCII only + CRLF: cmd.exe misreads UTF-8 text in LF-only batch files.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo [BALL x PIT helper] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py --ensure-mod
if errorlevel 1 (
    echo.
    echo [BALL x PIT helper] Mod install failed - screen recognition only.
    timeout /t 5 >nul
)
start "" ".venv\Scripts\pythonw.exe" main.py

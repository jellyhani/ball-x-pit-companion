@echo off
rem Start the helper (with mod check), then launch the game via Steam.
cd /d "%~dp0"
call run_overlay.bat
start "" "steam://rungameid/2062430"

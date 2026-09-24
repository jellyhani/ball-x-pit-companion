@echo off
rem Ask only this helper to quit. Other Python programs are not touched.
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py --stop

# BALL x PIT helper - first-time setup (Windows PowerShell 5.1 compatible, ASCII only).
#   1) Python environment (uv)   2) extract game text/icons from YOUR game install (not distributed)
#   3) BepInEx (downloaded from the official build server, SHA-256 checked) + bridge plugin
# Run:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'uv is not installed. Install it first, then run this again:'
    Write-Host '  winget install --id=astral-sh.uv -e      (or see https://docs.astral.sh/uv/)'
    exit 1
}
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    uv venv .venv --python 3.13
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
uv pip install --python .venv\Scripts\python.exe -r requirements.txt -r requirements-setup.txt
if ($LASTEXITCODE -ne 0) { exit 1 }

& .venv\Scripts\python.exe tools\setup_data.py @args
if ($LASTEXITCODE -ne 0) { exit 1 }

& .venv\Scripts\python.exe main.py --ensure-mod
Write-Host ''
Write-Host 'Done. Start the helper with run_overlay.bat (or play.bat to also launch the game).'

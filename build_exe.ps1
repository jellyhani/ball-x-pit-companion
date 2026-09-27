# Build the Windows exe (onedir) and a zip for GitHub Releases. ASCII only, PowerShell 5.1 compatible.
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot
uv pip install --python .venv\Scripts\python.exe -r requirements.txt -r requirements-setup.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { exit 1 }
& .venv\Scripts\python.exe -m unittest discover -s tests -t . -q
if ($LASTEXITCODE -ne 0) { Write-Host 'Tests failed - not building.'; exit 1 }
& .venv\Scripts\pyinstaller.exe BallxPitCompanion.spec --noconfirm
if ($LASTEXITCODE -ne 0) { exit 1 }
& .venv\Scripts\python.exe tools\verify_frozen.py dist\BallxPitCompanion\BallxPitCompanion.exe
if ($LASTEXITCODE -ne 0) { Write-Host 'Frozen runtime check failed - not packaging.'; exit 1 }
$ver = & .venv\Scripts\python.exe -c "from src.version import APP_VERSION; print(APP_VERSION)"
$zip = "dist\BallxPitCompanion-$ver.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\BallxPitCompanion -DestinationPath $zip
Write-Host "Built: dist\BallxPitCompanion\BallxPitCompanion.exe"
Write-Host "Zip:   $zip"

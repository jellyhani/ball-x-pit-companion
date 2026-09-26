# BALL x PIT bridge management. ASCII + CRLF for Windows PowerShell 5.1.
# Uninstall removes only BallxPitBridge.dll; the shared BepInEx runtime is retained.
param(
    [Parameter(Mandatory = $true)][ValidateSet("build", "install", "disable", "enable", "uninstall")][string]$Action,
    [string]$GameDir = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent (Split-Path -Parent $here)
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not $GameDir) {
    $GameDir = & $py -c "import sys; sys.path.insert(0, sys.argv[1]); from src.services.mod_installer import find_game_dir; print(find_game_dir() or '')" $root
    if ($LASTEXITCODE -ne 0 -or -not $GameDir) { throw "Game directory not found. Specify -GameDir." }
}
$GameDir = [IO.Path]::GetFullPath($GameDir)
$proj = Join-Path $here "BallxPitBridge"
function Assert-GameClosed {
    & $py -c "import sys; sys.path.insert(0, sys.argv[1]); from src.services.mod_installer import require_game_closed; require_game_closed()" $root
    if ($LASTEXITCODE -ne 0) { throw "Cannot confirm that the game is closed." }
}
switch ($Action) {
    "build" {
        dotnet build $proj -c Release -o (Join-Path $proj "bin") -p:GameDir="$GameDir"
        if ($LASTEXITCODE -ne 0) { throw "Build failed." }
        $vendor = Join-Path $root "vendor\bepinex"
        New-Item -ItemType Directory -Force $vendor | Out-Null
        Copy-Item -LiteralPath (Join-Path $proj "bin\BallxPitBridge.dll") -Destination (Join-Path $vendor "BallxPitBridge.dll") -Force
    }
    "install" {
        Assert-GameClosed
        if (-not (Test-Path -LiteralPath (Join-Path $GameDir "BepInEx\core"))) { throw "BepInEx is not installed." }
        & $MyInvocation.MyCommand.Path build -GameDir $GameDir
        $dst = Join-Path $GameDir "BepInEx\plugins"
        New-Item -ItemType Directory -Force $dst | Out-Null
        Copy-Item -LiteralPath (Join-Path $proj "bin\BallxPitBridge.dll") -Destination (Join-Path $dst "BallxPitBridge.dll") -Force
        Write-Host "Installed: $dst\BallxPitBridge.dll"
    }
    "disable" {
        $ini = Join-Path $GameDir "doorstop_config.ini"
        (Get-Content -LiteralPath $ini) -replace '^\s*enabled\s*=.*$', 'enabled = false' | Set-Content -LiteralPath $ini
        Write-Host "BepInEx disabled. This also disables any other BepInEx plugins."
    }
    "enable" {
        $ini = Join-Path $GameDir "doorstop_config.ini"
        (Get-Content -LiteralPath $ini) -replace '^\s*enabled\s*=.*$', 'enabled = true' | Set-Content -LiteralPath $ini
        Write-Host "BepInEx enabled."
    }
    "uninstall" {
        & $py -c "import sys; sys.path.insert(0, sys.argv[1]); from src.services.mod_installer import uninstall_bridge; print('Bridge removed.' if uninstall_bridge(sys.argv[2]) else 'Bridge already absent.')" $root $GameDir
        if ($LASTEXITCODE -ne 0) { throw "Bridge removal failed; shared runtime was retained." }
        Write-Host "Shared BepInEx runtime, other plugins, settings and logs retained."
    }
}

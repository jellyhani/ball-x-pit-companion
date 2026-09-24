# BALL x PIT 게임 연동(BepInEx 브리지) 관리
#   .\bridge.ps1 build       플러그인 빌드 (게임 업데이트 뒤 interop 이 바뀌면 다시 빌드)
#   .\bridge.ps1 install     빌드 후 게임 폴더 BepInEx\plugins 에 복사 (게임을 끈 상태에서)
#   .\bridge.ps1 disable     BepInEx 를 끈다 (doorstop_config.ini enabled=false). 파일은 남김
#   .\bridge.ps1 enable      다시 켠다
#   .\bridge.ps1 uninstall   이 도구가 게임 폴더에 추가한 파일만 지운다 (installed_files.txt 목록 기준)
param(
    [Parameter(Mandatory = $true)][ValidateSet("build", "install", "disable", "enable", "uninstall")][string]$Action,
    [string]$GameDir = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $GameDir) {
    # 게임 폴더 자동 찾기: 도우미와 같은 방법 (Steam 라이브러리에서 appid 2062430)
    $root = Split-Path -Parent (Split-Path -Parent $here)
    $py = Join-Path $root ".venv\Scripts\python.exe"
    $GameDir = & $py -c "import sys; sys.path.insert(0, r'$root'); from tools.extract_game_text import find_steam_game_dir; print(find_steam_game_dir() or '')"
    if (-not $GameDir) { throw "게임 폴더를 찾지 못했습니다. -GameDir 로 지정하세요." }
}
$proj = Join-Path $here "BallxPitBridge"
$manifest = Join-Path $here "installed_files.txt"

function Assert-GameClosed {
    if (Get-Process Balls -ErrorAction SilentlyContinue) { throw "게임을 먼저 종료하세요." }
}

switch ($Action) {
    "build" {
        dotnet build $proj -c Release -o (Join-Path $proj "bin") -p:GameDir="$GameDir"
        if ($LASTEXITCODE -ne 0) { throw "빌드 실패" }
        # 도우미 앱의 자동 설치가 쓰는 사본 (vendor\bepinex). 버전은 Plugin.cs 의 Plugin.Version.
        $vendor = Join-Path (Split-Path -Parent (Split-Path -Parent $here)) "vendor\bepinex"
        New-Item -ItemType Directory -Force $vendor | Out-Null
        Copy-Item (Join-Path $proj "bin\BallxPitBridge.dll") (Join-Path $vendor "BallxPitBridge.dll") -Force
    }
    "install" {
        Assert-GameClosed
        if (-not (Test-Path (Join-Path $GameDir "BepInEx\core"))) { throw "BepInEx 가 설치되어 있지 않습니다." }
        & $MyInvocation.MyCommand.Path build -GameDir $GameDir
        $dst = Join-Path $GameDir "BepInEx\plugins"
        New-Item -ItemType Directory -Force $dst | Out-Null
        Copy-Item (Join-Path $proj "bin\BallxPitBridge.dll") (Join-Path $dst "BallxPitBridge.dll") -Force
        Write-Host "설치 완료: $dst\BallxPitBridge.dll"
    }
    "disable" {
        $ini = Join-Path $GameDir "doorstop_config.ini"
        (Get-Content $ini) -replace '^enabled\s*=.*$', 'enabled = false' | Set-Content $ini
        Write-Host "BepInEx 꺼짐 (게임은 원래대로 실행됩니다)"
    }
    "enable" {
        $ini = Join-Path $GameDir "doorstop_config.ini"
        (Get-Content $ini) -replace '^enabled\s*=.*$', 'enabled = true' | Set-Content $ini
        Write-Host "BepInEx 켜짐"
    }
    "uninstall" {
        Assert-GameClosed
        $removed = 0
        foreach ($rel in Get-Content $manifest) {
            if (-not $rel.Trim()) { continue }
            $path = Join-Path $GameDir $rel
            if (Test-Path $path -PathType Leaf) { Remove-Item $path -Force; $removed++ }
        }
        # BepInEx 가 실행 중에 만든 폴더(interop, cache, config, 로그)와 비어 있는 폴더 정리
        foreach ($dir in "BepInEx", "dotnet") {
            $p = Join-Path $GameDir $dir
            if (Test-Path $p) { Remove-Item $p -Recurse -Force }
        }
        Write-Host "제거 완료: 파일 $removed개 + BepInEx, dotnet 폴더. 게임 원본 파일은 건드리지 않았습니다."
    }
}

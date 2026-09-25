# Build the native compute module: native\bxp_native.cpp -> src\engine\bxp_native.dll
# Needs Visual Studio 2022 (or Build Tools) with "Desktop development with C++" (MSVC x64).
# The built DLL is committed so source users do not need a C++ toolchain; without it the app uses Python.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "Visual Studio not found (vswhere.exe missing)" }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "Visual Studio with MSVC x64 tools not found" }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
$src = Join-Path $here "bxp_native.cpp"
$out = Join-Path $root "src\engine\bxp_native.dll"
$obj = Join-Path $env:TEMP "bxp_native_build"
New-Item -ItemType Directory -Force $obj | Out-Null
# /O2 optimize, /fp:precise (same floating point results as Python), /MT static CRT (no VC redist needed)
$cmd = "`"$vcvars`" >nul && cl /nologo /utf-8 /O2 /fp:precise /GS- /Zl /std:c++17 /LD /Fo`"$obj\bxp_native.obj`" `"$src`" /link /NODEFAULTLIB /NOENTRY /OUT:`"$out`" /NOIMPLIB /NOEXP"
cmd /c $cmd
if ($LASTEXITCODE -ne 0) { throw "build failed" }
Write-Host "Built: $out"

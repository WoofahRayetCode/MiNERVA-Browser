<#
.SYNOPSIS
    Build MiNERVA Archive Browser into a portable single-file .exe

.DESCRIPTION
    Checks for Python 3, installs it via Scoop if missing, creates a virtual
    environment, installs PyInstaller, and produces dist\MiNERVA-Browser.exe

.EXAMPLE
    .\build.ps1
    .\build.ps1 -SkipPythonCheck   # skip the Python install check
    .\build.ps1 -Clean             # delete build/ and dist/ first
#>
param(
    [switch]$Clean,
    [switch]$SkipPythonCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$VenvDir   = Join-Path $ScriptDir ".venv"
$DistDir   = Join-Path $ScriptDir "dist"
$BuildDir  = Join-Path $ScriptDir "build"
$SpecFile  = Join-Path $ScriptDir "minerva_browser.spec"
$OutExe    = Join-Path $DistDir "MiNERVA-Browser.exe"

# ── helpers ─────────────────────────────────────────────────────────────────

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Write-Ok([string]$msg) {
    Write-Host "    ✓ $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "    ! $msg" -ForegroundColor Yellow
}

function Find-Python {
    # Prefer a real Python over the Windows Store stub
    $candidates = @(
        (Get-Command python3 -ErrorAction SilentlyContinue)?.Source,
        (Get-Command python  -ErrorAction SilentlyContinue)?.Source
    ) | Where-Object { $_ -and $_ -notmatch "WindowsApps" }

    foreach ($p in $candidates) {
        $ver = & $p --version 2>&1
        if ($ver -match "Python 3\.(\d+)") {
            return $p
        }
    }

    # Scoop-installed python313 (preferred for libtorrent wheel compatibility)
    $scoopPython313 = "$env:USERPROFILE\scoop\apps\python313\current\python.exe"
    if (Test-Path $scoopPython313) { return $scoopPython313 }

    # Scoop-installed python (fallback)
    $scoopPython = "$env:USERPROFILE\scoop\apps\python\current\python.exe"
    if (Test-Path $scoopPython) { return $scoopPython }

    return $null
}

# ── optional clean ───────────────────────────────────────────────────────────

if ($Clean) {
    Write-Step "Cleaning previous build artifacts"
    foreach ($dir in @($BuildDir, $DistDir, $VenvDir)) {
        if (Test-Path $dir) {
            Remove-Item $dir -Recurse -Force
            Write-Ok "Removed $dir"
        }
    }
}

# ── ensure Python 3 ──────────────────────────────────────────────────────────

if (-not $SkipPythonCheck) {
    Write-Step "Checking for Python 3"
    $PythonExe = Find-Python

    if (-not $PythonExe) {
        Write-Warn "Python 3 not found. Installing via Scoop..."
        if (-not (Get-Command scoop -ErrorAction SilentlyContinue)) {
            throw "Scoop is not installed. Install Python 3 manually from https://python.org or install Scoop from https://scoop.sh and re-run."
        }
        scoop install python
        $PythonExe = Find-Python
        if (-not $PythonExe) {
            throw "Python still not found after Scoop install. Please install manually."
        }
    }

    $version = & $PythonExe --version 2>&1
    Write-Ok "Using: $PythonExe  ($version)"
} else {
    $PythonExe = Find-Python
    if (-not $PythonExe) { $PythonExe = "python3" }
}

# ── create / reuse venv ──────────────────────────────────────────────────────

Write-Step "Setting up virtual environment"

if (-not (Test-Path $VenvDir)) {
    & $PythonExe -m venv $VenvDir
    Write-Ok "Created venv at $VenvDir"
} else {
    Write-Ok "Reusing existing venv at $VenvDir"
}

$VenvPython      = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip         = Join-Path $VenvDir "Scripts\pip.exe"
$VenvPyInstaller = Join-Path $VenvDir "Scripts\pyinstaller.exe"

# ── install / upgrade PyInstaller ────────────────────────────────────────────

Write-Step "Installing PyInstaller"
& $VenvPip install --quiet --upgrade pip
& $VenvPip install --quiet pyinstaller
Write-Ok "PyInstaller ready"

# ── install libtorrent (optional) ────────────────────────────────────────────
Write-Step "Installing libtorrent (optional — enables inline torrent downloads)"
$ltOut = & $VenvPip install --quiet libtorrent 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Ok "libtorrent installed — downloads will be fully functional"
} else {
    Write-Warn "No libtorrent wheel available for this Python version."
    Write-Warn "The app will still build and run; downloads will show an install prompt."
    Write-Warn "To enable: use Python 3.10 or 3.13 and rebuild."
}

# ── stamp build version ──────────────────────────────────────────────────────

Write-Step "Stamping build version"
$BuildVersion = Get-Date -Format "yyyy.MMdd.HHmm"
$SourceFile   = Join-Path $ScriptDir "minerva_browser.py"
$SourceText   = Get-Content $SourceFile -Raw
$OriginalText = $SourceText
$SourceText   = $SourceText -replace '(?m)^APP_VERSION\s*=\s*"[^"]*"', "APP_VERSION = `"$BuildVersion`""
Set-Content -Path $SourceFile -Value $SourceText -NoNewline
Write-Ok "APP_VERSION = `"$BuildVersion`""

# ── build ────────────────────────────────────────────────────────────────────

Write-Step "Building portable executable"
Push-Location $ScriptDir
try {
    & $VenvPyInstaller $SpecFile --noconfirm
} finally {
    # Restore original source so version line stays clean in git
    Set-Content -Path $SourceFile -Value $OriginalText -NoNewline
    Pop-Location
}

# ── verify output ────────────────────────────────────────────────────────────

Write-Step "Verifying output"
if (Test-Path $OutExe) {
    $size = [math]::Round((Get-Item $OutExe).Length / 1MB, 1)
    Write-Ok "Built successfully: $OutExe  ($size MB)"
    Write-Host ""
    Write-Host "  Portable exe is ready — copy MiNERVA-Browser.exe anywhere and run it." -ForegroundColor White
} else {
    throw "Build finished but $OutExe was not found. Check PyInstaller output above."
}

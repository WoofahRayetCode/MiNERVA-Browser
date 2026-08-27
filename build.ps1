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
    [switch]$SkipPythonCheck,
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$VenvDir   = Join-Path $ScriptDir ".venv"
$DistDir   = Join-Path $ScriptDir "dist"
$BuildDir  = Join-Path $ScriptDir "build"
$SpecFile  = Join-Path $ScriptDir "minerva_browser.spec"
$OutExe    = Join-Path $DistDir "MiNERVA-Browser.exe"

# --- helpers -----------------------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Write-Ok([string]$msg) {
    Write-Host "    OK $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "    ! $msg" -ForegroundColor Yellow
}

function Get-CommandSource([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Find-Python {
    # Prefer a real Python over the Windows Store stub
    $candidates = @(
        (Get-CommandSource "python3"),
        (Get-CommandSource "python")
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

# --- optional clean ----------------------------------------------------------

if ($Clean) {
    Write-Step "Cleaning previous build artifacts"
    foreach ($dir in @($BuildDir, $DistDir, $VenvDir)) {
        if (Test-Path $dir) {
            Remove-Item $dir -Recurse -Force
            Write-Ok "Removed $dir"
        }
    }
}

# --- ensure Python 3 ---------------------------------------------------------

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

# --- create / reuse venv -----------------------------------------------------

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

# --- install / upgrade PyInstaller -------------------------------------------

Write-Step "Installing PyInstaller"
& $VenvPip install --quiet --upgrade pip
& $VenvPip install --quiet pyinstaller
Write-Ok "PyInstaller ready"

# --- install libtorrent (optional) -------------------------------------------
# Pip writes errors to stderr. With $ErrorActionPreference = Stop, that becomes
# a terminating NativeCommandError, so this step must temporarily allow failures.
Write-Step "Installing libtorrent (optional - enables inline torrent downloads)"
$oldEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $VenvPip install --quiet libtorrent 2>&1 | Out-Null
    $ltOk = ($LASTEXITCODE -eq 0)
} catch {
    $ltOk = $false
} finally {
    $ErrorActionPreference = $oldEap
}
if ($ltOk) {
    Write-Ok "libtorrent installed - downloads will be fully functional"
} else {
    Write-Warn "No libtorrent wheel available for this Python version."
    Write-Warn "The app will still build and run; downloads will show an install prompt."
    Write-Warn "To enable: use Python 3.10 or 3.13 and rebuild."
}

# --- download / verify chdman.exe (optional) ---------------------------------
Write-Step "Checking for chdman.exe (CHD disc compression tool)"
$ToolsDir = Join-Path $ScriptDir "tools\chdman"
$ChdmanExe = Join-Path $ToolsDir "chdman.exe"

if (Test-Path $ChdmanExe) {
    Write-Ok "chdman.exe already present at $ChdmanExe"
} else {
    Write-Step "Downloading chdman.exe automatically..."
    try {
        if (-not (Test-Path $ToolsDir)) {
            New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
        }
        $tmpDir = Join-Path $ScriptDir "_chdman_dl_tmp"
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

        $mameDevUrl = "https://www.mamedev.org/release.html"
        $html = (Invoke-WebRequest -Uri $mameDevUrl -UseBasicParsing -TimeoutSec 30).Content
        if ($html -match 'href="([^"]*mame\d+b_(?:x64|64bit)\.exe[^"]*)"') {
            $mameRelUrl = $matches[1]
            if (-not ($mameRelUrl -match "^https?://")) {
                $mameRelUrl = "https://www.mamedev.org/$mameRelUrl"
            }
            $mameInstaller = Join-Path $tmpDir "mame_installer.exe"
            Write-Host "    Downloading MAME package from $mameRelUrl ..." -ForegroundColor DarkGray
            Invoke-WebRequest -Uri $mameRelUrl -OutFile $mameInstaller -UseBasicParsing -TimeoutSec 120

            $sevenZipCmd = Get-Command 7z, 7za -ErrorAction SilentlyContinue | Select-Object -First 1
            $sevenZip = $null
            if ($sevenZipCmd) { $sevenZip = $sevenZipCmd.Source }
            if (-not $sevenZip -and (Test-Path "C:\Program Files\7-Zip\7z.exe")) { $sevenZip = "C:\Program Files\7-Zip\7z.exe" }

            if ($sevenZip) {
                & $sevenZip x -y "-o$tmpDir\extracted" $mameInstaller chdman.exe | Out-Null
            } else {
                Start-Process -FilePath $mameInstaller -ArgumentList "-y -o`"$tmpDir\extracted`"" -Wait -NoNewWindow
            }

            $extractedChd = Get-ChildItem -Path "$tmpDir\extracted" -Filter "chdman.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($extractedChd) {
                Copy-Item -Path $extractedChd.FullName -Destination $ChdmanExe -Force
                Write-Ok "chdman.exe downloaded and installed to $ChdmanExe"
            } else {
                Write-Warn "Could not find chdman.exe in extracted archive."
            }
        }
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
    } catch {
        Write-Warn "Automatic download of chdman.exe failed: $_"
    }
}

# --- run unit tests ----------------------------------------------------------
if (-not $SkipTests) {
    Write-Step "Running unit tests"
    & $VenvPython -m unittest discover -s (Join-Path $ScriptDir "tests") -v
    if ($LASTEXITCODE -ne 0) {
        throw "Unit tests failed. Build aborted."
    }
    Write-Ok "All unit tests passed"
}

# --- stamp build version -----------------------------------------------------

Write-Step "Stamping build version"
$BuildVersion = Get-Date -Format "yyyy.MMdd.HHmm"
$VersionFile  = Join-Path $ScriptDir "minerva\constants.py"
$VersionInit  = Join-Path $ScriptDir "minerva\__init__.py"
$OriginalConstants = Get-Content $VersionFile -Raw
$OriginalInit      = Get-Content $VersionInit -Raw
$NewConstants = $OriginalConstants -replace '(?m)^APP_VERSION\s*=\s*"[^"]*"', "APP_VERSION = `"$BuildVersion`""
$NewInit      = $OriginalInit -replace '(?m)^APP_VERSION\s*=\s*"[^"]*"', "APP_VERSION = `"$BuildVersion`""
Set-Content -Path $VersionFile -Value $NewConstants -NoNewline
Set-Content -Path $VersionInit -Value $NewInit -NoNewline
Write-Ok "APP_VERSION = `"$BuildVersion`""

# --- build -------------------------------------------------------------------

Write-Step "Building portable executable"
Push-Location $ScriptDir
try {
    & $VenvPyInstaller $SpecFile --noconfirm
} finally {
    # Restore original source so version lines stay clean in git
    Set-Content -Path $VersionFile -Value $OriginalConstants -NoNewline
    Set-Content -Path $VersionInit -Value $OriginalInit -NoNewline
    Pop-Location
}


# --- verify output -----------------------------------------------------------

Write-Step "Verifying output"
if (Test-Path $OutExe) {
    $size = [math]::Round((Get-Item $OutExe).Length / 1MB, 1)
    Write-Ok "Built successfully: $OutExe  ($size MB)"
    Write-Host ""
    Write-Host "  Portable exe is ready - copy MiNERVA-Browser.exe anywhere and run it." -ForegroundColor White
} else {
    throw "Build finished but $OutExe was not found. Check PyInstaller output above."
}

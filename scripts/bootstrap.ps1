# bootstrap.ps1 -- One-liner bootstrap for chat-to-cop on Windows.
#
# Invoke with:
#   irm https://raw.githubusercontent.com/act3-ace/chat-to-cop/main/scripts/bootstrap.ps1 | iex
#
# This script:
#   1. Checks for Python, Git, Ollama (does NOT reinstall existing tools)
#   2. Installs missing prerequisites via winget (with manual fallback)
#   3. Clones the repo (or updates if already cloned)
#   4. Runs setup and smoke test
#
# Safe to re-run: it skips anything already installed or cloned.

$ErrorActionPreference = "Continue"

function Write-Step($n, $msg) {
    Write-Host ""
    Write-Host "[$n] $msg" -ForegroundColor Cyan
    Write-Host ""
}

function Test-Command($cmd) {
    try { Get-Command $cmd -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

function Install-WithWinget($packageId, $name, $manualUrl) {
    if (-not $script:HasWinget) {
        Write-Host "  winget not available. Install $name manually:" -ForegroundColor Yellow
        Write-Host "  $manualUrl" -ForegroundColor Yellow
        Write-Host ""
        $script:ManualNeeded++
        return $false
    }

    Write-Host "  Installing $name via winget..."
    $result = Start-Process -FilePath "winget" -ArgumentList "install $packageId --accept-source-agreements --accept-package-agreements" -Wait -PassThru -NoNewWindow
    if ($result.ExitCode -ne 0) {
        Write-Host "  winget install failed (exit code $($result.ExitCode))." -ForegroundColor Yellow
        Write-Host "  This sometimes happens without admin rights." -ForegroundColor Yellow
        Write-Host "  Try: right-click terminal -> Run as Administrator, then re-run this script." -ForegroundColor Yellow
        Write-Host "  Or install manually: $manualUrl" -ForegroundColor Yellow
        Write-Host ""
        $script:ManualNeeded++
        return $false
    }

    Write-Host "  $name installed." -ForegroundColor Green

    # Refresh PATH for this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

    $script:Installed++
    return $true
}

# ---------------------------------------------------------------
# Header
# ---------------------------------------------------------------

Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  chat-to-cop bootstrap" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

$script:HasWinget = Test-Command "winget"
$script:Installed = 0
$script:ManualNeeded = 0
$NeedRestart = $false

# ---------------------------------------------------------------
# Step 1: Check and install prerequisites
# ---------------------------------------------------------------

Write-Step "1/3" "Checking prerequisites..."

# -- Python --
if (Test-Command "python") {
    $pyVer = (python --version 2>&1) -replace "Python ", ""
    Write-Host "  Python: $pyVer (already installed)"

    $parts = $pyVer.Split(".")
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
        Write-Host "  WARNING: Python 3.10+ required, you have $pyVer" -ForegroundColor Yellow
        $needPython = $true
    } else {
        $needPython = $false
    }
} else {
    Write-Host "  Python: not found"
    $needPython = $true
}

if ($needPython) {
    Install-WithWinget "Python.Python.3.12" "Python 3.12" "https://www.python.org/downloads/"
    $NeedRestart = $true
}

# -- Git --
if (Test-Command "git") {
    $gitVer = (git --version 2>&1) -replace "git version ", ""
    Write-Host "  Git: $gitVer (already installed)"
} else {
    Write-Host "  Git: not found"
    Install-WithWinget "Git.Git" "Git" "https://git-scm.com/downloads/win"
    $NeedRestart = $true
}

# -- Ollama --
if (Test-Command "ollama") {
    Write-Host "  Ollama: found (already installed)"
} else {
    Write-Host "  Ollama: not found"
    Install-WithWinget "Ollama.Ollama" "Ollama" "https://ollama.com/download"
    $NeedRestart = $true
}

# -- Docker (optional, just report) --
if (Test-Command "docker") {
    $dockVer = (docker --version 2>&1) -replace "Docker version ", ""
    Write-Host "  Docker: $dockVer (optional, found)"
} else {
    Write-Host "  Docker: not found (optional -- only needed for containerized deployment)"
}

# Check if we need manual intervention
if ($script:ManualNeeded -gt 0) {
    Write-Host ""
    Write-Host "$($script:ManualNeeded) tool(s) could not be installed automatically." -ForegroundColor Yellow
    Write-Host "Install them manually using the links above, then re-run this script." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# If we installed things, PATH may need a terminal restart
if ($NeedRestart -and $script:Installed -gt 0) {
    # Try refreshing PATH one more time
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# ---------------------------------------------------------------
# Step 2: Clone or update repo
# ---------------------------------------------------------------

Write-Step "2/3" "Getting the code..."

$repoUrl = "https://github.com/act3-ace/chat-to-cop.git"
$targetDir = Join-Path $env:USERPROFILE "chat-to-cop"

if (Test-Path (Join-Path $targetDir ".git")) {
    Write-Host "  Repo already cloned at $targetDir"
    Write-Host "  Pulling latest..."
    Push-Location $targetDir
    git pull --ff-only 2>&1 | ForEach-Object { Write-Host "  $_" }
    Pop-Location
} else {
    if (-not (Test-Command "git")) {
        Write-Host "  Git is not on PATH yet. Close this terminal, open a new one," -ForegroundColor Yellow
        Write-Host "  and re-run this script." -ForegroundColor Yellow
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "  Cloning to $targetDir..."
    git clone $repoUrl $targetDir 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Clone failed. Check your internet connection and try again." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ---------------------------------------------------------------
# Step 3: Run setup
# ---------------------------------------------------------------

Write-Step "3/3" "Running setup..."

Push-Location $targetDir
Write-Host "  Running run.bat --smoke-only to verify installation..."
Write-Host ""

cmd /c "run.bat --smoke-only"

Pop-Location

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Bootstrap complete" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  The repo is at: $targetDir"
Write-Host ""
Write-Host "  Next steps:"
Write-Host "    cd $targetDir"
Write-Host "    run.bat                    Full demo (smoke test + replay + dashboard)"
Write-Host "    run.bat --smoke-only       Quick 30-second verification"
Write-Host "    run.bat --live URL         Connect to live IRC server"
Write-Host ""

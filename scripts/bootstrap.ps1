# bootstrap.ps1 -- One-liner bootstrap for chat-to-cop on Windows.
#
# Invoke with (downloads to temp file, then executes as a proper script):
#
#   $f="$env:TEMP\chat-to-cop-bootstrap.ps1"; irm https://raw.githubusercontent.com/act3-ace/chat-to-cop/main/scripts/bootstrap.ps1 -OutFile $f; & $f; Remove-Item $f -ErrorAction SilentlyContinue
#
# This script:
#   1. Checks for Python, Git, Ollama (does NOT reinstall existing tools)
#   2. Installs missing prerequisites via winget (with manual fallback)
#   3. Clones the repo (or updates if already cloned)
#   4. Runs setup and smoke test
#
# Safe to re-run: it skips anything already installed or cloned.
#
# NOTE: If you saved this file and get an execution policy error when
# running it directly, use:
#   powershell -ExecutionPolicy Bypass -File bootstrap.ps1

$ErrorActionPreference = "Continue"

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

function Write-Step($n, $msg) {
    Write-Host ""
    Write-Host "[$n] $msg" -ForegroundColor Cyan
    Write-Host ""
}

function Test-Command($cmd) {
    try { Get-Command $cmd -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

function Refresh-SessionPath {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

function Install-WithWinget {
    param(
        [string]$PackageId,
        [string]$Name,
        [string]$ManualUrl,
        [bool]$HasWinget
    )

    if (-not $HasWinget) {
        Write-Host "  winget not available. Install $Name manually:" -ForegroundColor Yellow
        Write-Host "  $ManualUrl" -ForegroundColor Yellow
        Write-Host ""
        return "manual"
    }

    Write-Host "  Installing $Name via winget..."
    $result = Start-Process -FilePath "winget" `
        -ArgumentList "install $PackageId --accept-source-agreements --accept-package-agreements" `
        -Wait -PassThru -NoNewWindow
    if ($result.ExitCode -ne 0) {
        Write-Host "  winget install failed (exit code $($result.ExitCode))." -ForegroundColor Yellow
        Write-Host "  This sometimes happens without admin rights." -ForegroundColor Yellow
        Write-Host "  Try: right-click terminal -> Run as Administrator, then re-run this script." -ForegroundColor Yellow
        Write-Host "  Or install manually: $ManualUrl" -ForegroundColor Yellow
        Write-Host ""
        return "failed"
    }

    Write-Host "  $Name installed." -ForegroundColor Green
    Refresh-SessionPath
    return "ok"
}

# ---------------------------------------------------------------
# Header
# ---------------------------------------------------------------

Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  chat-to-cop bootstrap" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

$hasWinget = Test-Command "winget"
$installed = 0
$manualNeeded = 0
$needRestart = $false

# ---------------------------------------------------------------
# Step 1: Check and install prerequisites
# ---------------------------------------------------------------

Write-Step "1/3" "Checking prerequisites..."

# -- Python --
$needPython = $false
if (Test-Command "python") {
    $pyVer = (python --version 2>&1) -replace "Python ", ""
    Write-Host "  Python: $pyVer (already installed)"

    $parts = $pyVer.Split(".")
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
        Write-Host "  WARNING: Python 3.10+ required, you have $pyVer" -ForegroundColor Yellow
        $needPython = $true
    }
} else {
    Write-Host "  Python: not found"
    $needPython = $true
}

if ($needPython) {
    $r = Install-WithWinget -PackageId "Python.Python.3.12" -Name "Python 3.12" `
         -ManualUrl "https://www.python.org/downloads/" -HasWinget $hasWinget
    if ($r -eq "ok") { $installed++ }
    elseif ($r -eq "manual" -or $r -eq "failed") { $manualNeeded++ }
    $needRestart = $true
}

# -- Git --
if (Test-Command "git") {
    $gitVer = (git --version 2>&1) -replace "git version ", ""
    Write-Host "  Git: $gitVer (already installed)"
} else {
    Write-Host "  Git: not found"
    $r = Install-WithWinget -PackageId "Git.Git" -Name "Git" `
         -ManualUrl "https://git-scm.com/downloads/win" -HasWinget $hasWinget
    if ($r -eq "ok") { $installed++ }
    elseif ($r -eq "manual" -or $r -eq "failed") { $manualNeeded++ }
    $needRestart = $true
}

# -- Ollama --
if (Test-Command "ollama") {
    Write-Host "  Ollama: found (already installed)"
} else {
    Write-Host "  Ollama: not found"
    $r = Install-WithWinget -PackageId "Ollama.Ollama" -Name "Ollama" `
         -ManualUrl "https://ollama.com/download" -HasWinget $hasWinget
    if ($r -eq "ok") { $installed++ }
    elseif ($r -eq "manual" -or $r -eq "failed") { $manualNeeded++ }
    $needRestart = $true
}

# -- Docker (optional, just report) --
if (Test-Command "docker") {
    $dockVer = (docker --version 2>&1) -replace "Docker version ", ""
    Write-Host "  Docker: $dockVer (optional, found)"
} else {
    Write-Host "  Docker: not found (optional, needed only for containerized deployment)"
}

# Check if we need manual intervention
if ($manualNeeded -gt 0) {
    Write-Host ""
    Write-Host "$manualNeeded tool(s) could not be installed automatically." -ForegroundColor Yellow
    Write-Host "Install them manually using the links above, then re-run this script." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# If we installed things, refresh PATH one more time before continuing
if ($needRestart -and $installed -gt 0) {
    Refresh-SessionPath
}

# ---------------------------------------------------------------
# Step 2: Clone or update repo
# ---------------------------------------------------------------

Write-Step "2/3" "Getting the code..."

$repoUrl = "https://github.com/act3-ace/chat-to-cop.git"
$targetDir = Join-Path $env:USERPROFILE "chat-to-cop"

if (Test-Path (Join-Path $targetDir ".git")) {
    # Already cloned -- pull updates
    Write-Host "  Repo already cloned at $targetDir"
    Write-Host "  Pulling latest..."
    Push-Location $targetDir
    git pull --ff-only 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Pull failed (local changes or diverged branch)." -ForegroundColor Yellow
        Write-Host "  Your local copy may be out of date. Continuing anyway." -ForegroundColor Yellow
    }
    Pop-Location
} elseif (Test-Path $targetDir) {
    # Directory exists but is not a git repo (e.g., unzipped release)
    Write-Host "  $targetDir exists but is not a git clone." -ForegroundColor Yellow
    Write-Host "  Renaming to ${targetDir}.bak and cloning fresh..." -ForegroundColor Yellow
    Rename-Item $targetDir "${targetDir}.bak"
    if (-not (Test-Command "git")) {
        Write-Host "  Git is not on PATH yet. Close this terminal, reopen, and re-run." -ForegroundColor Yellow
        Read-Host "Press Enter to exit"
        exit 1
    }
    git clone $repoUrl $targetDir 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Clone failed. Check your internet connection and try again." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
} else {
    # Fresh clone
    if (-not (Test-Command "git")) {
        Write-Host "  Git is not on PATH yet. Close this terminal, reopen, and re-run." -ForegroundColor Yellow
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

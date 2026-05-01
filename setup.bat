@echo off
REM setup.bat -- Install prerequisites for chat-to-cop on Windows.
REM
REM This script checks for Python, Git, Ollama, and Docker and offers to
REM install any that are missing using winget (Windows Package Manager).
REM It will NOT reinstall or reconfigure anything already present.
REM
REM After prerequisites are installed, run "run.bat" to start the pipeline.
REM
REM Usage:
REM   setup.bat              - Interactive: check and install missing prereqs
REM   setup.bat --check      - Just check what's installed, don't install anything
REM   setup.bat --help       - Show this help

setlocal enabledelayedexpansion

cd /d "%~dp0"

if "%1"=="--help" goto :help
if "%1"=="-h" goto :help

set CHECK_ONLY=0
if "%1"=="--check" set CHECK_ONLY=1

echo.
echo ============================================
echo   chat-to-cop prerequisite check
echo ============================================
echo.

REM Track what's missing
set MISSING_PYTHON=0
set MISSING_GIT=0
set MISSING_OLLAMA=0
set MISSING_DOCKER=0
set HAS_WINGET=0

REM -------------------------------------------------------------------
REM Check winget availability
REM -------------------------------------------------------------------

winget --version >nul 2>&1
if not errorlevel 1 (
    set HAS_WINGET=1
)

REM -------------------------------------------------------------------
REM Check Python
REM -------------------------------------------------------------------

python --version >nul 2>&1
if errorlevel 1 (
    echo   Python:  NOT FOUND
    set MISSING_PYTHON=1
) else (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo   Python:  !PYVER!

    REM Check version is 3.10+
    for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
        if %%a LSS 3 (
            echo            WARNING: Python 3.10+ required, you have !PYVER!
            set MISSING_PYTHON=1
        ) else if %%a EQU 3 (
            if %%b LSS 10 (
                echo            WARNING: Python 3.10+ required, you have !PYVER!
                set MISSING_PYTHON=1
            )
        )
    )
)

REM -------------------------------------------------------------------
REM Check Git
REM -------------------------------------------------------------------

git --version >nul 2>&1
if errorlevel 1 (
    echo   Git:     NOT FOUND
    set MISSING_GIT=1
) else (
    for /f "tokens=3 delims= " %%v in ('git --version 2^>^&1') do set GITVER=%%v
    echo   Git:     !GITVER!
)

REM -------------------------------------------------------------------
REM Check Ollama
REM -------------------------------------------------------------------

ollama --version >nul 2>&1
if errorlevel 1 (
    echo   Ollama:  NOT FOUND
    set MISSING_OLLAMA=1
) else (
    echo   Ollama:  found
)

REM -------------------------------------------------------------------
REM Check Docker (optional)
REM -------------------------------------------------------------------

docker --version >nul 2>&1
if errorlevel 1 (
    echo   Docker:  not found (optional)
    set MISSING_DOCKER=1
) else (
    for /f "tokens=3 delims= " %%v in ('docker --version 2^>^&1') do set DOCKVER=%%v
    echo   Docker:  !DOCKVER!
)

REM -------------------------------------------------------------------
REM Summary
REM -------------------------------------------------------------------

echo.

set /a TOTAL_MISSING=MISSING_PYTHON + MISSING_GIT + MISSING_OLLAMA
if %TOTAL_MISSING% EQU 0 (
    echo All required prerequisites are installed.
    echo.
    echo Run "run.bat" to start the pipeline.
    echo.
    if %CHECK_ONLY% EQU 1 pause
    exit /b 0
)

if %CHECK_ONLY% EQU 1 (
    echo Missing %TOTAL_MISSING% required prerequisite(s).
    if %MISSING_PYTHON% EQU 1 echo   - Python 3.10+
    if %MISSING_GIT% EQU 1    echo   - Git
    if %MISSING_OLLAMA% EQU 1 echo   - Ollama
    echo.
    pause
    exit /b 1
)

REM -------------------------------------------------------------------
REM Install missing prerequisites
REM -------------------------------------------------------------------

echo Missing %TOTAL_MISSING% required prerequisite(s). Attempting to install...
echo.

if %HAS_WINGET% EQU 0 (
    echo   winget (Windows Package Manager) is not available on this machine.
    echo   This can happen on older Windows versions or enterprise-locked systems.
    echo.
    echo   Please install the missing tools manually:
    echo.
    if %MISSING_PYTHON% EQU 1 (
        echo   Python 3.12:  https://www.python.org/downloads/
        echo                 IMPORTANT: Check "Add Python to PATH" during install.
        echo.
    )
    if %MISSING_GIT% EQU 1 (
        echo   Git:          https://git-scm.com/downloads/win
        echo                 Accept all defaults during install.
        echo.
    )
    if %MISSING_OLLAMA% EQU 1 (
        echo   Ollama:       https://ollama.com/download
        echo                 Click "Download for Windows" and run the installer.
        echo.
    )
    echo   After installing, close and reopen this terminal, then run this
    echo   script again to verify.
    echo.
    pause
    exit /b 1
)

REM -------------------------------------------------------------------
REM Install with winget
REM -------------------------------------------------------------------

set INSTALL_COUNT=0
set INSTALL_FAILED=0

if %MISSING_PYTHON% EQU 1 (
    echo --------------------------------------------
    echo   Installing Python 3.12...
    echo --------------------------------------------
    echo.
    winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo   Python install failed. You may need to run this terminal as
        echo   Administrator, or install manually from:
        echo   https://www.python.org/downloads/
        echo.
        set /a INSTALL_FAILED+=1
    ) else (
        echo   Python installed.
        set /a INSTALL_COUNT+=1
    )
    echo.
)

if %MISSING_GIT% EQU 1 (
    echo --------------------------------------------
    echo   Installing Git...
    echo --------------------------------------------
    echo.
    winget install Git.Git --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo   Git install failed. You may need to run this terminal as
        echo   Administrator, or install manually from:
        echo   https://git-scm.com/downloads/win
        echo.
        set /a INSTALL_FAILED+=1
    ) else (
        echo   Git installed.
        set /a INSTALL_COUNT+=1
    )
    echo.
)

if %MISSING_OLLAMA% EQU 1 (
    echo --------------------------------------------
    echo   Installing Ollama...
    echo --------------------------------------------
    echo.
    winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo   Ollama install failed. You may need to run this terminal as
        echo   Administrator, or install manually from:
        echo   https://ollama.com/download
        echo.
        set /a INSTALL_FAILED+=1
    ) else (
        echo   Ollama installed.
        set /a INSTALL_COUNT+=1
    )
    echo.
)

REM -------------------------------------------------------------------
REM Post-install
REM -------------------------------------------------------------------

echo ============================================
echo   Setup complete
echo ============================================
echo.

if %INSTALL_COUNT% GTR 0 (
    echo   Installed %INSTALL_COUNT% package(s).
)
if %INSTALL_FAILED% GTR 0 (
    echo   %INSTALL_FAILED% package(s) failed to install (see above).
)

if %INSTALL_COUNT% GTR 0 (
    echo.
    echo   IMPORTANT: Close and reopen this terminal so that newly installed
    echo   programs are on your PATH, then run "run.bat" to start the pipeline.
) else if %INSTALL_FAILED% EQU 0 (
    echo   Everything was already installed. Run "run.bat" to start.
)

echo.
pause

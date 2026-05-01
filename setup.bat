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
        set /a PY_MAJOR=%%a 2>nul
        set /a PY_MINOR=%%b 2>nul
    )
    if !PY_MAJOR! LSS 3 (
        echo            WARNING: Python 3.10+ required, you have !PYVER!
        set MISSING_PYTHON=1
    ) else if !PY_MAJOR! EQU 3 if !PY_MINOR! LSS 10 (
        echo            WARNING: Python 3.10+ required, you have !PYVER!
        set MISSING_PYTHON=1
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
REM Check Docker (optional, report only)
REM -------------------------------------------------------------------

docker --version >nul 2>&1
if errorlevel 1 (
    echo   Docker:  not found (optional)
) else (
    for /f "tokens=3 delims= " %%v in ('docker --version 2^>^&1') do set DOCKVER=%%v
    echo   Docker:  !DOCKVER!
)

REM -------------------------------------------------------------------
REM Summary
REM -------------------------------------------------------------------

echo.

set /a TOTAL_MISSING=MISSING_PYTHON + MISSING_GIT + MISSING_OLLAMA
if !TOTAL_MISSING! EQU 0 (
    echo All required prerequisites are installed.
    echo.
    echo Run "run.bat" to start the pipeline.
    echo.
    if !CHECK_ONLY! EQU 1 pause
    exit /b 0
)

if !CHECK_ONLY! EQU 1 (
    echo Missing !TOTAL_MISSING! required prerequisite(s^).
    if !MISSING_PYTHON! EQU 1 echo   - Python 3.10+
    if !MISSING_GIT! EQU 1    echo   - Git
    if !MISSING_OLLAMA! EQU 1 echo   - Ollama
    echo.
    pause
    exit /b 1
)

REM -------------------------------------------------------------------
REM Install missing prerequisites
REM -------------------------------------------------------------------

echo Missing !TOTAL_MISSING! required prerequisite(s^). Attempting to install...
echo.

if !HAS_WINGET! EQU 0 (
    echo   winget (Windows Package Manager^) is not available on this machine.
    echo   This can happen on older Windows versions or enterprise-locked systems.
    echo.
    echo   Please install the missing tools manually:
    echo.
    if !MISSING_PYTHON! EQU 1 (
        echo   Python 3.12:  https://www.python.org/downloads/
        echo                 IMPORTANT: Check "Add Python to PATH" during install.
        echo.
    )
    if !MISSING_GIT! EQU 1 (
        echo   Git:          https://git-scm.com/downloads/win
        echo                 Accept all defaults during install.
        echo.
    )
    if !MISSING_OLLAMA! EQU 1 (
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
REM Install with winget (each install in its own call to avoid
REM errorlevel-inside-compound-block issues)
REM -------------------------------------------------------------------

set INSTALL_COUNT=0
set INSTALL_FAILED=0

if !MISSING_PYTHON! EQU 1 call :do_install Python.Python.3.12 "Python 3.12" "https://www.python.org/downloads/"
if !MISSING_GIT! EQU 1    call :do_install Git.Git "Git" "https://git-scm.com/downloads/win"
if !MISSING_OLLAMA! EQU 1 call :do_install Ollama.Ollama "Ollama" "https://ollama.com/download"

REM -------------------------------------------------------------------
REM Post-install
REM -------------------------------------------------------------------

echo ============================================
echo   Setup complete
echo ============================================
echo.

if !INSTALL_COUNT! GTR 0 (
    echo   Installed !INSTALL_COUNT! package(s^).
)
if !INSTALL_FAILED! GTR 0 (
    echo   !INSTALL_FAILED! package(s^) failed to install (see above^).
)

if !INSTALL_COUNT! GTR 0 (
    echo.
    echo   Newly installed programs need a fresh terminal to appear on PATH.
    echo   Press any key to close, then open a new terminal and run "run.bat".
) else if !INSTALL_FAILED! EQU 0 (
    echo   Everything was already installed. Run "run.bat" to start.
)

echo.
if !INSTALL_COUNT! GTR 0 (
    pause
    exit
) else (
    pause
)
endlocal
exit /b 0

REM ===================================================================
REM Subroutines
REM ===================================================================

:do_install
REM %1 = winget package ID, %~2 = display name, %~3 = manual URL
echo --------------------------------------------
echo   Installing %~2...
echo   (A permissions prompt may appear -- please approve it.)
echo --------------------------------------------
echo.
winget install %1 --accept-source-agreements --accept-package-agreements
set _WINGET_ERR=!errorlevel!
if !_WINGET_ERR! NEQ 0 (
    echo.
    echo   %~2 install failed (exit code !_WINGET_ERR!^).
    echo   You may need to run this terminal as Administrator,
    echo   or install manually from: %~3
    echo.
    set /a INSTALL_FAILED+=1
) else (
    echo   %~2 installed.
    set /a INSTALL_COUNT+=1
)
echo.
goto :eof

REM -------------------------------------------------------------------
REM Help
REM -------------------------------------------------------------------

:help
echo.
echo setup.bat -- Install prerequisites for chat-to-cop
echo.
echo Usage:
echo   setup.bat              Check and install missing prerequisites
echo   setup.bat --check      Just check what's installed, don't install
echo   setup.bat --help       Show this help
echo.
echo Checks for: Python 3.10+, Git, Ollama
echo Installs missing tools via winget (Windows Package Manager).
echo Will NOT reinstall or reconfigure anything already present.
echo.
echo If winget is not available, prints manual download links instead.
echo.
pause
goto :eof

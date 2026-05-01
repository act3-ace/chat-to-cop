@echo off
REM run.bat -- One-click launcher for chat-to-cop on Windows.
REM
REM Double-click this file or run it from a terminal. It will:
REM   1. Check that Python and Ollama are available
REM   2. Install the package if needed
REM   3. Pull and configure the LLM model
REM   4. Run the smoke test
REM   5. Replay the bundled DASH 3 sample
REM   6. Start the dashboard and open it in your browser
REM
REM Usage:
REM   run.bat                 - Full setup + demo replay
REM   run.bat --smoke-only    - Just run the smoke test
REM   run.bat --dashboard     - Start the dashboard only
REM   run.bat --live URL      - Connect to a live IRC server

setlocal enabledelayedexpansion

cd /d "%~dp0"

if "%1"=="--help" goto :help
if "%1"=="-h" goto :help

REM -------------------------------------------------------------------
REM Preflight checks
REM -------------------------------------------------------------------

echo.
echo ============================================
echo   chat-to-cop setup
echo ============================================
echo.
echo [1/4] Checking prerequisites...
echo.

REM -------------------------------------------------------------------
REM Check Python
REM -------------------------------------------------------------------

python --version >nul 2>&1
if errorlevel 1 (
    echo   Python: NOT FOUND
    echo.
    echo   Run "setup.bat" to install prerequisites automatically,
    echo   or install Python 3.12 manually from https://www.python.org/downloads/
    echo   (check "Add Python to PATH" during install).
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   Python: %PYVER%

REM -------------------------------------------------------------------
REM Check Git
REM -------------------------------------------------------------------

git --version >nul 2>&1
if errorlevel 1 (
    echo   Git: NOT FOUND
    echo.
    echo   Run "setup.bat" to install prerequisites automatically,
    echo   or install Git manually from https://git-scm.com/downloads/win
    echo.
    pause
    exit /b 1
)

for /f "tokens=3 delims= " %%v in ('git --version 2^>^&1') do set GITVER=%%v
echo   Git: %GITVER%

REM -------------------------------------------------------------------
REM Check Ollama
REM -------------------------------------------------------------------

ollama --version >nul 2>&1
if errorlevel 1 (
    echo   Ollama: NOT FOUND
    echo.
    echo   Ollama runs the local AI model.
    echo   Install: run "setup.bat" or download from https://ollama.com/download
    echo.
    set /p SKIP_OLLAMA="Skip Ollama and use a cloud backend instead? [y/N] "
    if /i "!SKIP_OLLAMA!" neq "y" (
        exit /b 1
    )
    set OLLAMA_OK=0
) else (
    echo   Ollama: found
    set OLLAMA_OK=1
)

REM -------------------------------------------------------------------
REM Check Docker (optional, for containerized deployment)
REM -------------------------------------------------------------------

docker --version >nul 2>&1
if errorlevel 1 (
    echo   Docker: not found (optional -- needed only for containerized deployment)
) else (
    for /f "tokens=3 delims= " %%v in ('docker --version 2^>^&1') do set DOCKVER=%%v
    echo   Docker: !DOCKVER!
)

echo.

REM -------------------------------------------------------------------
REM Install package if needed
REM -------------------------------------------------------------------

echo [2/4] Checking chat-to-cop installation...

python -c "from chat_to_cop.models.cop_update import CoPUpdate" >nul 2>&1
if errorlevel 1 (
    echo   Installing package (first run only, may take a minute)...
    python -m pip install -e ".[dev]"
    if errorlevel 1 (
        echo.
        echo ERROR: Installation failed. Try running manually:
        echo   python -m pip install -e ".[dev]"
        echo.
        echo If you see "Microsoft Visual C++ required", you may need to install
        echo the Visual Studio Build Tools. But this should not normally happen.
        pause
        exit /b 1
    )
    echo   Installed successfully.
) else (
    echo   chat-to-cop: installed
)

REM -------------------------------------------------------------------
REM Pull and configure Ollama model
REM -------------------------------------------------------------------

if "%OLLAMA_OK%"=="1" (
    echo.
    echo [chat-to-cop] Checking Ollama model...

    REM Check if Ollama is serving
    curl -s http://127.0.0.1:11434/v1/models >nul 2>&1
    if errorlevel 1 (
        echo   Ollama is installed but not running.
        echo   Starting Ollama...
        start "" ollama serve
        timeout /t 5 /nobreak >nul
    )

    REM Check if the 8K model exists
    ollama list 2>nul | findstr /c:"qwen2.5:7b-8k" >nul 2>&1
    if errorlevel 1 (
        echo   Pulling qwen2.5:7b (this may take a few minutes on first run)...
        ollama pull qwen2.5:7b
        echo   Creating 8K context variant...
        ollama create qwen2.5:7b-8k -f deploy\ollama\Modelfile.7b
    ) else (
        echo   Model qwen2.5:7b-8k: ready
    )
)

REM -------------------------------------------------------------------
REM Route to the requested mode
REM -------------------------------------------------------------------

if "%1"=="--smoke-only" goto :smoke
if "%1"=="--dashboard" goto :dashboard
if "%1"=="--live" goto :live

REM Default: smoke test + replay + dashboard

:smoke
echo.
echo ============================================
echo   Running smoke test (5 messages)
echo ============================================
echo.
python scripts/quick_test.py
if errorlevel 1 (
    echo.
    echo Smoke test failed. Check the errors above.
    if "%1"=="--smoke-only" pause
    if "%1"=="--smoke-only" exit /b 1
)

if "%1"=="--smoke-only" (
    echo.
    echo Smoke test passed.
    pause
    exit /b 0
)

echo.
echo ============================================
echo   Replaying DASH 3 sample data
echo ============================================
echo.
echo Processing bundled chat messages through the pipeline...
echo (This takes 2-5 minutes depending on your hardware)
echo.

python -m chat_to_cop.replay data\dash3\23Sep_usaf_chat.zip --db data\demo_run.db

:dashboard
echo.
echo ============================================
echo   Starting dashboard
echo ============================================
echo.

REM Open browser after a short delay
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000/docs"

echo Dashboard starting at http://localhost:8000/docs
echo Press Ctrl+C to stop.
echo.
uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000
goto :eof

:live
if "%2"=="" (
    echo ERROR: --live requires an IRC server URL.
    echo Usage: run.bat --live ws://IRC_SERVER_IP:8097
    pause
    exit /b 1
)
echo.
echo ============================================
echo   Connecting to live IRC: %2
echo ============================================
echo.
set CHAT_TO_COP_IRC_URL=%2
set CHAT_TO_COP_LLM_URL=http://127.0.0.1:11434/v1
set CHAT_TO_COP_LLM_MODEL=qwen2.5:7b-8k
set CHAT_TO_COP_DB_PATH=data\mash_live.db
python -m chat_to_cop.replay
goto :eof

REM -------------------------------------------------------------------
REM Help
REM -------------------------------------------------------------------

:help
echo.
echo chat-to-cop -- AI staff officer for MASH wargame events
echo.
echo Usage:
echo   run.bat                     Full setup: smoke test + demo replay + dashboard
echo   run.bat --smoke-only        Just verify everything works (30 seconds)
echo   run.bat --dashboard         Start the REST API dashboard only
echo   run.bat --live URL          Connect to a live IRC server
echo.
echo Prerequisites:
echo   - Python 3.10+  (https://www.python.org/downloads/)
echo   - Ollama        (https://ollama.com/download) -- optional for cloud backends
echo.
echo First run will install dependencies and pull the LLM model (~4.7 GB).
echo Subsequent runs start in seconds.
echo.
pause
goto :eof

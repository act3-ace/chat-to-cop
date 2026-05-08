@echo off
REM run.bat -- One-click launcher for chat-to-cop on Windows.
REM
REM Double-click this file or run it from a terminal. It will:
REM   1. Check that Python and Ollama are available
REM   2. Detect your GPU and pick the best model
REM   3. Install the package if needed
REM   4. Pull and configure the LLM model
REM   5. Run the smoke test
REM   6. Replay the bundled DASH 3 sample
REM   7. Start the dashboard and open it in your browser
REM
REM Usage:
REM   run.bat                 - Full setup + demo replay
REM   run.bat --smoke-only    - Just run the smoke test
REM   run.bat --dashboard     - Start the dashboard only
REM   run.bat --live URL      - Connect to a live IRC server
REM   run.bat --cloud URL     - Use a remote LLM backend (skip local Ollama)

setlocal enabledelayedexpansion

cd /d "%~dp0"

if not exist data mkdir data

REM -------------------------------------------------------------------
REM Logging -- write key diagnostics to data\run.log
REM -------------------------------------------------------------------

set LOGFILE=data\run.log
echo. >> "%LOGFILE%"
echo ================================================================ >> "%LOGFILE%"
echo   run.bat started: %DATE% %TIME% >> "%LOGFILE%"
echo   Arguments: %* >> "%LOGFILE%"
echo   User: %USERNAME% >> "%LOGFILE%"
echo   Machine: %COMPUTERNAME% >> "%LOGFILE%"
echo ================================================================ >> "%LOGFILE%"

if "%1"=="--help" goto :help
if "%1"=="-h" goto :help

REM Save args before call :auto_update_pull -- the call+goto pattern
REM inside the subroutine corrupts %1/%2 on some cmd.exe versions.
set _ARG1=%1
set _ARG2=%2

REM -------------------------------------------------------------------
REM Parse --cloud argument
REM -------------------------------------------------------------------

set CLOUD_URL=
set USE_CLOUD=0
if "%_ARG1%"=="--cloud" (
    if "%_ARG2%"=="" (
        echo ERROR: --cloud requires a URL.
        echo Usage: run.bat --cloud http://REMOTE_IP:PORT/v1
        pause
        exit /b 1
    )
    set CLOUD_URL=%_ARG2%
    set USE_CLOUD=1
    echo   Using remote LLM backend: %_ARG2%
    echo   [cloud] URL=%_ARG2% >> "%LOGFILE%"
)

REM -------------------------------------------------------------------
REM Preflight checks
REM -------------------------------------------------------------------

echo.
echo ============================================
echo   chat-to-cop setup
echo ============================================
echo.

REM -------------------------------------------------------------------
REM Auto-update: pull latest if git clone, or convert zip to clone
REM -------------------------------------------------------------------

if exist ".git" (
    call :auto_update_pull
) else (
    call :auto_update_convert
)

:after_auto_update

echo [1/5] Checking prerequisites...
echo.

REM -------------------------------------------------------------------
REM Check Python
REM -------------------------------------------------------------------

python --version >nul 2>&1
if errorlevel 1 (
    echo   Python: NOT FOUND
    echo   [FAIL] Python not found >> "%LOGFILE%"
    echo.
    echo   Run "setup.bat" to install prerequisites automatically,
    echo   or install Python 3.12 manually from https://www.python.org/downloads/
    echo   ^(check "Add Python to PATH" during install^).
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   Python: %PYVER%
echo   [OK] Python %PYVER% >> "%LOGFILE%"

REM -------------------------------------------------------------------
REM Check Git
REM -------------------------------------------------------------------

git --version >nul 2>&1
if errorlevel 1 (
    echo   Git: NOT FOUND
    echo   [FAIL] Git not found >> "%LOGFILE%"
    echo.
    echo   Run "setup.bat" to install prerequisites automatically,
    echo   or install Git manually from https://git-scm.com/downloads/win
    echo.
    pause
    exit /b 1
)

for /f "tokens=3 delims= " %%v in ('git --version 2^>^&1') do set GITVER=%%v
echo   Git: %GITVER%
echo   [OK] Git %GITVER% >> "%LOGFILE%"

REM -------------------------------------------------------------------
REM Check Ollama (skip if using cloud or live mode)
REM -------------------------------------------------------------------

set OLLAMA_OK=0
if "%USE_CLOUD%"=="1" (
    echo   Ollama: skipped ^(using remote backend^)
    echo   [SKIP] Ollama -- cloud mode >> "%LOGFILE%"
    goto :skip_ollama_check
)
if "%_ARG1%"=="--live" (
    echo   Ollama: skipped ^(live mode -- uses env vars or localhost default^)
    echo   [SKIP] Ollama -- live mode >> "%LOGFILE%"
    goto :skip_ollama_check
)

ollama --version >nul 2>&1
if errorlevel 1 (
    echo   Ollama: NOT FOUND
    echo   [WARN] Ollama not found >> "%LOGFILE%"
    echo.
    echo   Ollama runs the local AI model and is needed for the demo.
    echo   To install: run "setup.bat" or download from https://ollama.com/download
    echo.
    echo   If you can't install Ollama, ask Scott for a remote server URL
    echo   and re-run as:  run.bat --cloud http://SERVER_IP:PORT/v1
    echo.
    set /p SKIP_OLLAMA="Continue without Ollama? (advanced -- only if you have a cloud LLM) [y/N] "
    if /i "!SKIP_OLLAMA!" neq "y" (
        exit /b 1
    )
) else (
    echo   Ollama: found
    echo   [OK] Ollama found >> "%LOGFILE%"
    set OLLAMA_OK=1
)

:skip_ollama_check

REM In live mode, skip GPU detection and model setup -- just need the package
set RECOMMENDED_MODEL=qwen2.5:7b-8k
if "%_ARG1%"=="--live" goto :skip_gpu_detection

REM -------------------------------------------------------------------
REM GPU detection -- pick the best model for this hardware
REM -------------------------------------------------------------------

echo.
echo [2/5] Detecting GPU...

set GPU_NAME=none
set GPU_VRAM_MB=0
set RECOMMENDED_MODEL=qwen2.5:7b-8k
set RECOMMENDED_PULL=qwen2.5:7b
set MODELFILE=deploy\ollama\Modelfile.7b

nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits >nul 2>&1
if errorlevel 1 (
    echo   GPU: No NVIDIA GPU detected
    echo   [WARN] No NVIDIA GPU >> "%LOGFILE%"
    if "%USE_CLOUD%"=="0" (
        echo.
        echo   WARNING: Without a GPU, local inference will be very slow ^(5-30x^).
        echo   Consider using a remote backend instead:
        echo     run.bat --cloud http://REMOTE_SERVER:PORT/v1
        echo.
        echo   Falling back to smallest model ^(qwen2.5:3b^) for CPU...
        set RECOMMENDED_MODEL=qwen2.5:3b
        set RECOMMENDED_PULL=qwen2.5:3b
        set MODELFILE=
    )
) else (
    for /f "tokens=1,2 delims=," %%a in ('nvidia-smi --query-gpu^=name^,memory.total --format^=csv^,noheader^,nounits 2^>nul') do (
        set GPU_NAME=%%a
        set GPU_VRAM_MB=%%b
    )
    REM Trim whitespace from VRAM
    for /f "tokens=*" %%x in ("!GPU_VRAM_MB!") do set GPU_VRAM_MB=%%x
    echo   GPU: !GPU_NAME! ^(!GPU_VRAM_MB! MB VRAM^)
    echo   [OK] GPU: !GPU_NAME!, !GPU_VRAM_MB! MB VRAM >> "%LOGFILE%"

    REM Pick model based on VRAM
    REM 14B needs ~10GB (10240 MB), 7B needs ~6GB (6144 MB), 3B needs ~3GB
    if !GPU_VRAM_MB! GEQ 10240 (
        echo   Recommended model: qwen2.5:14b ^(best quality for your GPU^)
        set RECOMMENDED_MODEL=qwen2.5:14b-8k
        set RECOMMENDED_PULL=qwen2.5:14b
        set MODELFILE=deploy\ollama\Modelfile.14b
    ) else if !GPU_VRAM_MB! GEQ 6144 (
        echo   Recommended model: qwen2.5:7b ^(good match for your GPU^)
        set RECOMMENDED_MODEL=qwen2.5:7b-8k
        set RECOMMENDED_PULL=qwen2.5:7b
        set MODELFILE=deploy\ollama\Modelfile.7b
    ) else (
        echo   Recommended model: qwen2.5:3b ^(your GPU has limited VRAM^)
        set RECOMMENDED_MODEL=qwen2.5:3b
        set RECOMMENDED_PULL=qwen2.5:3b
        set MODELFILE=
    )
    echo   [MODEL] !RECOMMENDED_MODEL! >> "%LOGFILE%"
)

REM -------------------------------------------------------------------
REM Check Docker (optional, for containerized deployment)
REM -------------------------------------------------------------------

docker --version >nul 2>&1
if errorlevel 1 (
    echo   Docker: not found ^(optional -- needed only for containerized deployment^)
) else (
    for /f "tokens=3 delims= " %%v in ('docker --version 2^>^&1') do set DOCKVER=%%v
    echo   Docker: !DOCKVER!
)

echo.

:skip_gpu_detection

REM -------------------------------------------------------------------
REM Refresh supplemental glossary from live DELTRON (falls back to cached)
REM -------------------------------------------------------------------

if not defined CHAT_TO_COP_GLOSSARY_FILE (
    echo   Refreshing glossary from DELTRON...
    python scripts\bootstrap_mash_glossary.py >nul 2>&1
    if errorlevel 1 (
        if exist data\mash_glossary.txt (
            echo   DELTRON unreachable -- using cached glossary
            echo   [WARN] DELTRON unreachable, using cached glossary >> "%LOGFILE%"
        ) else (
            echo   DELTRON unreachable -- no glossary available
            echo   [WARN] DELTRON unreachable, no glossary >> "%LOGFILE%"
        )
    ) else (
        echo   Glossary refreshed from DELTRON
        echo   [OK] Glossary refreshed from DELTRON >> "%LOGFILE%"
    )
    if exist data\mash_glossary.txt (
        set CHAT_TO_COP_GLOSSARY_FILE=data\mash_glossary.txt
    )
)

REM -------------------------------------------------------------------
REM Install package if needed
REM -------------------------------------------------------------------

echo [3/5] Checking chat-to-cop installation...

REM Ensure pip is available (some Python installs omit it)
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo   pip not found. Bootstrapping pip...
    python -m ensurepip --upgrade
    echo   [FIX] Bootstrapped pip via ensurepip >> "%LOGFILE%"
)

python -c "from chat_to_cop.models.cop_update import CoPUpdate" >nul 2>&1
if errorlevel 1 (
    REM Check for bundled wheels first (offline install)
    if exist vendor\*.whl (
        echo   Installing package from bundled wheels ^(offline^)...
        python -m pip install --no-index --find-links vendor\ -e ".[dev]"
        echo   [INSTALL] Used vendor wheels >> "%LOGFILE%"
    ) else (
        echo   Installing package ^(first run only, may take 2-5 minutes^)...
        python -m pip install -e ".[dev]"
    )
    if errorlevel 1 (
        echo.
        echo ERROR: Installation failed. Try running manually:
        echo   python -m pip install -e ".[dev]"
        echo.
        echo If you see "Microsoft Visual C++ required", you may need to install
        echo the Visual Studio Build Tools. But this should not normally happen.
        echo   [FAIL] pip install failed >> "%LOGFILE%"
        pause
        exit /b 1
    )
    echo   Installed successfully.
    echo   [OK] Package installed >> "%LOGFILE%"
) else (
    echo   chat-to-cop: installed
)

REM -------------------------------------------------------------------
REM Configure LLM backend (skip in live mode -- uses env vars or defaults)
REM -------------------------------------------------------------------

if "%_ARG1%"=="--live" goto :skip_ollama_setup

if "%USE_CLOUD%"=="1" goto :setup_cloud_backend

if "%OLLAMA_OK%" neq "1" goto :skip_ollama_setup

echo.
echo [4/5] Checking Ollama model...

REM Check if Ollama is serving; start it if not, with retry
curl -s http://127.0.0.1:11434/v1/models >nul 2>&1
if errorlevel 1 (
    echo   Ollama is installed but not running.
    echo   Starting Ollama...
    start "" ollama serve
    call :wait_for_ollama
)

REM Check if the recommended model exists
ollama list 2>nul | findstr /c:"!RECOMMENDED_MODEL!" >nul 2>&1
if errorlevel 1 (
    echo   Pulling !RECOMMENDED_PULL! ^(this may take a few minutes on first run^)...
    ollama pull !RECOMMENDED_PULL!
    if defined MODELFILE (
        echo   Creating 8K context variant...
        ollama create !RECOMMENDED_MODEL! -f !MODELFILE!
    )
) else (
    echo   Model !RECOMMENDED_MODEL!: ready
)

REM Set env vars for the pipeline
set CHAT_TO_COP_LLM_URL=http://127.0.0.1:11434/v1
set CHAT_TO_COP_LLM_MODEL=!RECOMMENDED_MODEL!
echo   [BACKEND] Ollama local: !RECOMMENDED_MODEL! >> "%LOGFILE%"

goto :skip_cloud_setup

:setup_cloud_backend
echo.
echo [4/5] Checking remote backend...

REM Verify the remote backend is reachable
curl -s --max-time 10 "%CLOUD_URL%/models" >nul 2>&1
if errorlevel 1 (
    curl -s --max-time 10 "%CLOUD_URL%" >nul 2>&1
    if errorlevel 1 (
        echo   WARNING: Remote backend at %CLOUD_URL% is not responding.
        echo   [WARN] Cloud backend unreachable: %CLOUD_URL% >> "%LOGFILE%"
        echo   The smoke test may fail. Check the URL and try again.
        echo.
        set /p CONTINUE_CLOUD="Continue anyway? [y/N] "
        if /i "!CONTINUE_CLOUD!" neq "y" exit /b 1
    )
)

echo   Remote backend: %CLOUD_URL%
set CHAT_TO_COP_LLM_URL=%CLOUD_URL%

REM Auto-detect model from remote backend
if not defined CHAT_TO_COP_LLM_MODEL (
    echo   Detecting available models...
    for /f "usebackq delims=" %%m in (`python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('%CLOUD_URL%/models')); print(d['data'][0]['id'])" 2^>nul`) do (
        set CHAT_TO_COP_LLM_MODEL=%%m
    )
    if not defined CHAT_TO_COP_LLM_MODEL set CHAT_TO_COP_LLM_MODEL=qwen2.5:7b
    echo   Detected model: !CHAT_TO_COP_LLM_MODEL!
)
echo   [BACKEND] Cloud: %CLOUD_URL%, model: !CHAT_TO_COP_LLM_MODEL! >> "%LOGFILE%"

:skip_cloud_setup
:skip_ollama_setup

REM -------------------------------------------------------------------
REM Route to the requested mode
REM -------------------------------------------------------------------

if "%_ARG1%"=="--smoke-only" goto :smoke
if "%_ARG1%"=="--dashboard" goto :dashboard
if "%_ARG1%"=="--live" goto :live

REM Default: smoke test + replay + dashboard

:smoke

REM Quick connectivity check before committing to the smoke test.
REM Without this, 5 LLM calls x 120s timeout = 10 minutes of apparent freeze.
if "%USE_CLOUD%"=="0" if "!OLLAMA_OK!"=="1" (
    curl -s --max-time 5 http://127.0.0.1:11434/v1/models >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   WARNING: Ollama is not responding at http://127.0.0.1:11434
        echo   The smoke test will likely fail or hang.
        echo   Try starting Ollama manually: open Start menu, search "Ollama", click it.
        echo   Then re-run this script.
        echo.
        set /p SKIP_SMOKE="Continue anyway? [y/N] "
        if /i "!SKIP_SMOKE!" neq "y" exit /b 1
    )
)

echo.
echo ============================================
echo   Running smoke test (5 messages)
echo ============================================
echo.
python scripts\quick_test.py --url "!CHAT_TO_COP_LLM_URL!" --model "!CHAT_TO_COP_LLM_MODEL!"
if errorlevel 1 (
    echo.
    echo ============================================
    echo   Smoke test failed
    echo ============================================
    echo.
    if "%USE_CLOUD%"=="0" (
        echo   Your local LLM backend did not respond correctly.
        echo   Common fixes:
        echo     1. Make sure Ollama is running ^(open Start menu, search "Ollama"^)
        echo     2. Try a smaller model:  ollama pull qwen2.5:3b
        echo     3. Use a remote server instead:
        echo.
        echo        run.bat --cloud http://SERVER_IP:PORT/v1
        echo.
        echo   Ask Scott for a remote server URL if you need one.
    ) else (
        echo   The remote backend at !CLOUD_URL! did not respond correctly.
        echo   Check that the URL is correct and the server is running.
        echo   Ask Scott if the server needs to be restarted.
    )
    echo.
    echo   Full log saved to: %CD%\%LOGFILE%
    echo   [FAIL] Smoke test failed >> "%LOGFILE%"
    if "%_ARG1%"=="--smoke-only" goto :health_summary
)

echo   [OK] Smoke test passed >> "%LOGFILE%"

if "%_ARG1%"=="--smoke-only" goto :health_summary

echo.
echo ============================================
echo   Replaying DASH 3 sample data
echo ============================================
echo.
echo Processing bundled chat messages through the pipeline...
echo (This takes 2-5 minutes depending on your hardware)
echo.

python -m chat_to_cop.replay data\dash3\23Sep_usaf_chat.zip --db data\demo_run.db --url "!CHAT_TO_COP_LLM_URL!" --model "!CHAT_TO_COP_LLM_MODEL!"

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
if "%_ARG2%"=="" (
    echo ERROR: --live requires an IRC server URL.
    echo Usage: run.bat --live ws://IRC_SERVER_IP:8097
    pause
    exit /b 1
)
echo.
echo ============================================
echo   Connecting to live IRC: %_ARG2%
echo ============================================
echo.
set CHAT_TO_COP_IRC_URL=%_ARG2%
if not defined CHAT_TO_COP_LLM_URL set CHAT_TO_COP_LLM_URL=http://127.0.0.1:11434/v1
if not defined CHAT_TO_COP_LLM_MODEL set CHAT_TO_COP_LLM_MODEL=!RECOMMENDED_MODEL!
set CHAT_TO_COP_DB_PATH=data\mash_live.db
python -m chat_to_cop.replay
goto :eof

REM -------------------------------------------------------------------
REM Health summary
REM -------------------------------------------------------------------

:health_summary
echo.
echo ============================================
echo   Health Summary
echo ============================================
echo.
echo   Python:    %PYVER%
echo   GPU:       !GPU_NAME! (!GPU_VRAM_MB! MB)
echo   Backend:   !CHAT_TO_COP_LLM_URL!
echo   Model:     !CHAT_TO_COP_LLM_MODEL!
if "%USE_CLOUD%"=="1" (
    echo   Mode:      Remote ^(cloud^)
) else (
    echo   Mode:      Local ^(Ollama^)
)
echo   Log file:  %CD%\%LOGFILE%
echo.
echo   [SUMMARY] Python=%PYVER% GPU=!GPU_NAME! Backend=!CHAT_TO_COP_LLM_URL! Model=!CHAT_TO_COP_LLM_MODEL! >> "%LOGFILE%"
echo   run.bat finished: %DATE% %TIME% >> "%LOGFILE%"
echo.
echo If something went wrong, send data\run.log to the team for help.
echo.
pause
exit /b 0

REM -------------------------------------------------------------------
REM Subroutines
REM -------------------------------------------------------------------

:wait_for_ollama
REM Wait up to 30 seconds for Ollama to start responding.
set /a _OLLAMA_TRIES=0
:ollama_retry
set /a _OLLAMA_TRIES+=1
if !_OLLAMA_TRIES! GTR 6 (
    echo   Ollama did not respond after 30 seconds.
    echo   Try starting it manually: open Start menu, search "Ollama", click it.
    echo   [WARN] Ollama did not start after 30s >> "%LOGFILE%"
    goto :eof
)
timeout /t 5 /nobreak >nul
curl -s --max-time 3 http://127.0.0.1:11434/v1/models >nul 2>&1
if errorlevel 1 (
    echo   Waiting for Ollama to start... (!_OLLAMA_TRIES!/6)
    goto :ollama_retry
)
echo   Ollama is running.
goto :eof

:auto_update_pull
REM Already a git clone -- pull latest changes.
REM Uses goto :after_auto_update (not goto :eof) so that if git pull
REM changes this file, the goto re-scans from file start and lands
REM correctly despite the stale call-return byte offset.
git --version >nul 2>&1
if errorlevel 1 goto :after_auto_update
echo   Checking for updates...
git pull --ff-only >nul 2>&1
if errorlevel 1 (
    echo   Could not auto-update -- running current version.
    echo   [WARN] git pull failed >> "%LOGFILE%"
) else (
    echo   Updated to latest version.
    echo   [OK] git pull succeeded >> "%LOGFILE%"
)
echo.
goto :after_auto_update

:auto_update_convert
REM Not a git clone -- try to convert extracted zip into a clone
REM so future runs get auto-updates.
git --version >nul 2>&1
if errorlevel 1 goto :after_auto_update
echo   Setting up auto-updates...
echo   [AUTO-UPDATE] Converting zip to git clone >> "%LOGFILE%"
git init >nul 2>&1
if errorlevel 1 goto :convert_failed
git remote add origin git@gitlab.dle.afrl.af.mil:c2es1/mash/chat-to-cop.git >nul 2>&1
if errorlevel 1 goto :convert_failed
echo   Connecting to code server (may take a moment)...
git fetch --depth 1 origin main >nul 2>&1
if errorlevel 1 goto :convert_failed
git checkout -f -B main origin/main >nul 2>&1
if errorlevel 1 goto :convert_failed
echo   Auto-updates enabled -- future runs will pull the latest code.
echo   [OK] Converted zip to git clone >> "%LOGFILE%"
echo.
goto :after_auto_update

:convert_failed
if exist ".git" rmdir /s /q .git >nul 2>&1
echo   Could not connect to code server -- continuing without auto-updates.
echo   ^(This is fine. Everything still works, you just won't get automatic updates.^)
echo   [WARN] zip-to-clone conversion failed >> "%LOGFILE%"
echo.
goto :after_auto_update

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
echo   run.bat --cloud URL         Use a remote LLM backend (skip local Ollama)
echo.
echo GPU Detection:
echo   The script detects your NVIDIA GPU and picks the best model:
echo     10+ GB VRAM  -^>  qwen2.5:14b (best quality)
echo      6+ GB VRAM  -^>  qwen2.5:7b  (good quality)
echo      No GPU       -^>  qwen2.5:3b  (CPU fallback, slow)
echo.
echo Cloud Backend (remote server):
echo   run.bat --cloud http://SERVER_IP:PORT/v1
echo   Skips Ollama entirely. Uses a remote LLM server for all inference.
echo   The model name is auto-detected from the server.
echo   Ask Scott for the server URL if your local setup is not working.
echo.
echo Prerequisites:
echo   - Python 3.10+  (https://www.python.org/downloads/)
echo   - Ollama        (https://ollama.com/download) -- not needed with --cloud
echo.
echo First run will install dependencies and pull the LLM model (~4.7 GB).
echo Subsequent runs start in seconds.
echo.
echo Troubleshooting:
echo   All runs are logged to data\run.log. Send this file to the team
echo   if you run into problems. Common issues:
echo     - "Connection error" -- Ollama is not running. Start it from the
echo       Start menu, or use --cloud with a remote server.
echo     - Slow inference -- Your GPU may be too small. Try --cloud.
echo     - pip install fails -- Run "python -m pip install -e ." manually.
echo.
pause
goto :eof

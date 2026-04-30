@echo off
REM Start the chat-to-cop portable pipeline on Windows.
REM
REM Usage:
REM   start.bat              - GPU mode (default)
REM   start.bat --no-gpu     - CPU-only mode
REM   start.bat --stop       - shut down all services

setlocal

cd /d "%~dp0"

if "%1"=="--stop" goto :stop
if "%1"=="--help" goto :help
if "%1"=="-h" goto :help

REM -------------------------------------------------------------------
REM Preflight
REM -------------------------------------------------------------------

echo [chat-to-cop] Checking Docker...
docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker is not running. Start Docker Desktop and try again.
    exit /b 1
)

REM -------------------------------------------------------------------
REM Load images
REM -------------------------------------------------------------------

echo [chat-to-cop] Loading Docker images from tarballs...

for %%f in (images\*.tar) do (
    echo   Loading %%~nxf...
    docker load -i "%%f"
)

echo [chat-to-cop] Images loaded.

REM -------------------------------------------------------------------
REM Create data directory
REM -------------------------------------------------------------------

if not exist data mkdir data

REM -------------------------------------------------------------------
REM Handle GPU / CPU mode
REM -------------------------------------------------------------------

if "%1"=="--no-gpu" goto :cpu_mode

REM GPU mode (default)
if exist docker-compose.override.yml del docker-compose.override.yml
docker compose up -d
goto :started

:cpu_mode
echo [chat-to-cop] Running in CPU-only mode (no GPU acceleration).
echo [chat-to-cop] LLM inference will be slower. First response may take 30-60 seconds.
echo.

(
echo services:
echo   ollama:
echo     deploy: {}
) > docker-compose.override.yml

docker compose -f docker-compose.yml -f docker-compose.override.yml up -d
goto :started

REM -------------------------------------------------------------------
REM Started
REM -------------------------------------------------------------------

:started
echo.
echo ============================================
echo   chat-to-cop is starting up
echo ============================================
echo.
echo   Services:
echo     Ollama (LLM):     http://localhost:11434
echo     Mock IRC server:  ws://localhost:8097
echo     REST API:         http://localhost:8001/docs
echo.
echo   The pipeline is processing canned demo messages.
echo   Watch extraction output:
echo     docker compose logs -f pipeline
echo.
echo   To stop everything:
echo     start.bat --stop
echo.
echo   First startup takes 30-60 seconds while the LLM loads.
echo   Run 'docker compose logs -f' to see all service logs.
echo.
goto :eof

REM -------------------------------------------------------------------
REM Stop
REM -------------------------------------------------------------------

:stop
echo [chat-to-cop] Stopping all services...
docker compose down
echo [chat-to-cop] Stopped.
goto :eof

REM -------------------------------------------------------------------
REM Help
REM -------------------------------------------------------------------

:help
echo Usage: start.bat [--no-gpu] [--stop]
echo   --no-gpu   Run without GPU acceleration (slower but works anywhere)
echo   --stop     Shut down all services
goto :eof

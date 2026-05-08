@echo off
REM Expose chat-to-cop API + dashboard on the network.
REM Double-click this or run in a second terminal while the pipeline is running.

setlocal enabledelayedexpansion

REM Match the DB that "run.bat --live" writes to.
REM If you only ran the demo (not --live), it reads world_state.db instead.
set CHAT_TO_COP_DB_PATH=data\world_state.db

REM Auto-detect supplemental glossary (built by bootstrap_mash_glossary.py)
if not defined CHAT_TO_COP_GLOSSARY_FILE (
    if exist data\mash_glossary.txt (
        set CHAT_TO_COP_GLOSSARY_FILE=data\mash_glossary.txt
        echo   Glossary: data\mash_glossary.txt ^(auto-detected^)
    )
)
if exist data\mash_live.db set CHAT_TO_COP_DB_PATH=data\mash_live.db

REM Auto-detect network IP (prefer 10.5.x MASH network, then any 10.x, then 192.168.x)
set MY_IP=localhost
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"10.5."') do (
    for /f "tokens=*" %%b in ("%%a") do set MY_IP=%%b
)
if "!MY_IP!"=="localhost" (
    for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"10."') do (
        for /f "tokens=*" %%b in ("%%a") do if "!MY_IP!"=="localhost" set MY_IP=%%b
    )
)
if "!MY_IP!"=="localhost" (
    for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"192.168."') do (
        for /f "tokens=*" %%b in ("%%a") do if "!MY_IP!"=="localhost" set MY_IP=%%b
    )
)

echo.
echo  chat-to-cop API server
echo  ----------------------
echo.
echo  YOUR ENDPOINT (give this to vendors):
echo.
echo    http://!MY_IP!:8000/updates
echo.
echo  Other URLs:
echo    Dashboard:  http://!MY_IP!:8000/dashboard
echo    Swagger:    http://!MY_IP!:8000/docs
echo    Health:     http://!MY_IP!:8000/health
echo.
echo  Database: %CHAT_TO_COP_DB_PATH%
echo.

uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000

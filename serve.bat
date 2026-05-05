@echo off
REM Expose chat-to-cop API + dashboard on the network.
REM Double-click this or run in a second terminal while the pipeline is running.

set CHAT_TO_COP_DB_PATH=data\world_state.db
if exist data\mash_live.db set CHAT_TO_COP_DB_PATH=data\mash_live.db

echo.
echo  chat-to-cop API server
echo  ----------------------
echo  Dashboard:  http://localhost:8000/dashboard
echo  Swagger:    http://localhost:8000/docs
echo  Updates:    http://localhost:8000/updates
echo.
echo  For others on the network, replace "localhost" with your IP.
echo  Run: ipconfig -- look for 10.5.185.x
echo.

uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000

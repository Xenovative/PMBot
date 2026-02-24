@echo off
setlocal

REM Paths
set BACKEND_DIR=%~dp0m5-backend
set FRONTEND_DIR=%~dp0m5-frontend
set BACKEND_PORT=8889
set FRONTEND_PORT=5176

REM Backend venv
if not exist "%BACKEND_DIR%\venv" (
    echo Creating backend venv...
    py -3.12 -m venv "%BACKEND_DIR%\venv" || goto :error
)

REM Install backend deps if needed
if not exist "%BACKEND_DIR%\venv\Scripts\pip.exe" goto :error
"%BACKEND_DIR%\venv\Scripts\pip" install -q -r "%BACKEND_DIR%\requirements.txt" || goto :error

REM Start backend in new window
start "m5-backend" cmd /k "cd /d %BACKEND_DIR% && set PORT=%BACKEND_PORT% && ..\m5-backend\venv\Scripts\python main.py"

REM Install frontend deps
cd /d "%FRONTEND_DIR%"
if not exist node_modules (
    npm install || goto :error
)

REM Start frontend in new window
start "m5-frontend" cmd /k "cd /d %FRONTEND_DIR% && npm run dev -- --host --port %FRONTEND_PORT%"

echo Started m5 backend on port %BACKEND_PORT% and frontend on port %FRONTEND_PORT%.
goto :eof

:error
echo Failed to start. Check above logs.
exit /b 1

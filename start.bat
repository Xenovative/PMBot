@echo off
chcp 65001 >nul
echo ========================================
echo   Polymarket Bot Starting...
echo ========================================

echo.
echo Cleaning up stale processes...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8888 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5173 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
timeout /t 1 /nobreak >nul

set "ROOT=%~dp0"
set "PY_EXE=C:\Users\Cyber Beast Tech\AppData\Local\Programs\Python\Python312\python.exe"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo Creating venv with %PY_EXE% ...
  "%PY_EXE%" -m venv "%ROOT%.venv"
)

echo Ensuring dependencies...
"%VENV_PY%" -m pip install -q --upgrade pip
"%VENV_PY%" -m pip install -q -r "%ROOT%backend\requirements.txt"

echo [1/2] Starting backend (port 8888)...
start "PMBot-Backend" /D "%~dp0backend" cmd /c ""%VENV_PY%" main.py"

timeout /t 3 /nobreak >nul

echo [2/2] Building frontend...
pushd "%~dp0frontend" >nul
call npm run build >nul
popd >nul

echo [2/2] Starting frontend (port 5173)...
start "PMBot-Frontend" /D "%~dp0frontend" npm run dev

timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   Started!
echo   Frontend: http://localhost:5173
echo   Backend:  http://localhost:8888
echo ========================================
echo.
echo Press any key to stop all services...
pause >nul

echo Stopping services...
REM Kill by port to ensure Python/Node processes actually die
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8888 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5173 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq PMBot-Backend" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq PMBot-Frontend" /F >nul 2>&1
echo Done.

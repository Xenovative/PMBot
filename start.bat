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

echo [1/2] Starting backend (port 8888)...
start "PMBot-Backend" /D "%~dp0backend" "C:\Users\Cyber Beast Tech\AppData\Local\Programs\Python\Python312\python.exe" main.py

timeout /t 3 /nobreak >nul

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

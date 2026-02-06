@echo off
chcp 65001 >nul
echo ========================================
echo   Polymarket Daily Bot Starting...
echo ========================================

echo.
echo Cleaning up stale processes...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8889 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5174 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
timeout /t 1 /nobreak >nul

echo [1/2] Starting backend (port 8889)...
start "PMBot-Daily-Backend" /D "%~dp0daily-backend" "C:\Users\Cyber Beast Tech\AppData\Local\Programs\Python\Python312\python.exe" main.py

timeout /t 3 /nobreak >nul

echo [2/2] Starting frontend (port 5174)...
start "PMBot-Daily-Frontend" /D "%~dp0daily-frontend" npm run dev

timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   Started!
echo   Frontend: http://localhost:5174
echo   Backend:  http://localhost:8889
echo ========================================
echo.
echo Press any key to stop all services...
pause >nul

echo Stopping services...
REM Kill by port to ensure Python/Node processes actually die
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8889 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5174 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq PMBot-Daily-Backend" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq PMBot-Daily-Frontend" /F >nul 2>&1
echo Done.

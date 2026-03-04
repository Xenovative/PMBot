@echo off
chcp 65001 >nul
echo ========================================
echo   Polymarket Hourly Bot Starting...
echo ========================================

echo.
echo Cleaning up stale processes...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8890 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5175 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
timeout /t 1 /nobreak >nul

echo [1/2] Starting backend (port 8890)...
start "PMBot-Hourly-Backend" /D "%~dp0hourly-backend" "C:\Users\Cyber Beast Tech\AppData\Local\Programs\Python\Python312\python.exe" main.py

timeout /t 3 /nobreak >nul

echo [2/2] Starting frontend (port 5175)...
start "PMBot-Hourly-Frontend" /D "%~dp0hourly-frontend" npm run dev

timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   Started!
echo   Frontend: http://localhost:5175
echo   Backend:  http://localhost:8890
echo ========================================
echo.
echo Press any key to stop all services...
pause >nul

echo Stopping services...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8890 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5175 " ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq PMBot-Hourly-Backend" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq PMBot-Hourly-Frontend" /F >nul 2>&1
echo Done.

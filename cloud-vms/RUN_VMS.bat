@echo off
REM =====================================================================
REM  Gatehouse Cloud VMS - one-click launcher (double-click me)
REM   1. checks every prerequisite and installs only what is missing
REM      (install_prerequisites.bat - fast when everything is present)
REM   2. starts the backend + camera workers in a "Cloud VMS server" window
REM   3. waits until the server answers, then opens the dashboard in a
REM      new Google Chrome tab (default browser if Chrome is unavailable)
REM  To stop the system, close the "Cloud VMS server" window.
REM =====================================================================
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Gatehouse VMS launcher
set "PORT=8000"
set "URL=http://localhost:%PORT%/"
REM Health checks use 127.0.0.1: "localhost" tries IPv6 (::1) first, and on
REM Windows a refused connection takes ~2 s, which made the old check time out
REM on every attempt - so the launcher never reached the "open Chrome" step.
set "HEALTH=http://127.0.0.1:%PORT%/api/health"

REM ---------- 1. prerequisites: check first, install only what is missing
set "VMS_CHROME="
if exist "install_prerequisites.bat" goto :have_installer
echo The project files are missing next to RUN_VMS.bat.
echo Extract the WHOLE zip first (right-click the zip - Extract All), then double-click
echo RUN_VMS.bat inside the extracted folder.
pause
exit /b 1
:have_installer
call "%~dp0install_prerequisites.bat" nopause
if errorlevel 1 goto :setup_failed
cd /d "%~dp0"

REM ---------- 2. already running? then just open the browser
call :is_up
if not errorlevel 1 (
  echo The server is already running.
  goto :open
)

REM free the port if an old, hung Cloud VMS (python) instance still holds it
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /c:":%PORT% " ^| findstr LISTENING') do (
  call :kill_if_python %%p
)

echo Starting the server in a new window ...
start "Cloud VMS server" /D "%~dp0backend" cmd /k "..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port %PORT%"

echo Waiting for it to be ready (the first start can take a minute or two) ...
set /a tries=0
:wait
call :sleep 2
call :is_up
if not errorlevel 1 goto :started
set /a tries+=1
if %tries% lss 150 goto :wait
echo.
echo The server did not answer within 5 minutes. Look at the "Cloud VMS server" window for the error.
pause
exit /b 1

:started
echo Server is up.
if not exist "data\initial_admin_password.txt" goto :open
echo.
echo ---- first sign-in ----
type "data\initial_admin_password.txt"
echo -----------------------

REM ---------- 3. open the dashboard in a new Chrome tab
:open
set "CHROME=%VMS_CHROME%"
if defined CHROME if not exist "%CHROME%" set "CHROME="
if not defined CHROME call :find_chrome
if defined CHROME goto :chrome
echo Google Chrome not found - opening your default browser instead.
start "" "%URL%"
goto :done
:chrome
echo Opening %URL% in Google Chrome ...
REM With Chrome already running this adds a new tab to the open window;
REM otherwise it starts Chrome with the dashboard.
start "" "%CHROME%" "%URL%"

:done
echo.
echo Dashboard: %URL%
echo Keep the "Cloud VMS server" window open while you use the system; close it to stop.
call :sleep 10
exit /b 0

:setup_failed
echo.
echo Setup did not finish. Read the messages above, fix the problem and double-click RUN_VMS.bat again.
pause
exit /b 1


REM ---------- helper: errorlevel 0 when the API answers
:is_up
where curl.exe >nul 2>nul
if errorlevel 1 goto :is_up_ps
curl.exe -s -f -o nul --max-time 5 "%HEALTH%" >nul 2>nul
exit /b %errorlevel%
:is_up_ps
powershell -NoProfile -Command "try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 '%HEALTH%').StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
exit /b %errorlevel%

REM ---------- helper: stop PID %1 only if it is a python process (an old server)
:kill_if_python
tasklist /fi "PID eq %~1" 2>nul | find /i "python" >nul
if not errorlevel 1 taskkill /PID %~1 /F >nul 2>nul
exit /b 0

REM ---------- helper: wait N seconds (works even when input is redirected, unlike timeout)
:sleep
set /a _n=%~1+1
ping -n %_n% 127.0.0.1 >nul 2>nul
exit /b 0

REM ---------- helper: CHROME = path of chrome.exe (fallback if the installer did not report it)
:find_chrome
for %%F in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LocalAppData%\Google\Chrome\Application\chrome.exe") do (
  if not defined CHROME if exist "%%~F" set "CHROME=%%~F"
)
if defined CHROME exit /b 0
for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul ^| find "REG_"') do if exist "%%~B" set "CHROME=%%~B"
if defined CHROME exit /b 0
for /f "tokens=2,*" %%A in ('reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul ^| find "REG_"') do if exist "%%~B" set "CHROME=%%~B"
exit /b 0

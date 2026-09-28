@echo off
REM =====================================================================
REM  Gatehouse Cloud VMS - prerequisite checker / installer (Windows)
REM
REM  RUN_VMS.bat calls this automatically on every launch. You can also
REM  double-click it on its own.
REM
REM  Every item is CHECKED first and installed ONLY if it is missing:
REM    1. 64-bit Windows and a complete (extracted) project folder
REM    2. Python 3.10 - 3.12            (winget, else python.org installer)
REM    3. Microsoft Visual C++ runtime  (needed by PyTorch / OpenCV)
REM    4. Google Chrome                 (the dashboard opens in Chrome)
REM    5. Node.js                       (only if frontend\dist is missing)
REM    6. Project virtual environment   .venv
REM    7. Python packages, model weights, demo video, .env, web build
REM       (scripts\check_setup.py - same check-then-install rule)
REM
REM  Exit code 0 = ready to run, 1 = something required could not be set up.
REM  When it finishes it leaves VMS_CHROME set for the caller (RUN_VMS.bat).
REM =====================================================================
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "ROOT=%CD%"
set "NOPAUSE=%~1"
set "DL=%TEMP%\gatehouse_vms_setup"
set "PY_VERSION=3.12.10"
set "HAVE_WINGET=0"
where winget >nul 2>nul
if not errorlevel 1 set "HAVE_WINGET=1"

echo.
echo ================= Checking prerequisites =================

REM ---------------------------------------------------------------- 1. sanity
if exist "backend\requirements.txt" if exist "scripts\check_setup.py" if exist "scripts\probe_python.py" goto :folder_ok
echo   [ERROR]     Project files are missing next to this script.
echo               Extract the WHOLE zip first ^(right-click - Extract All^), then run
echo               RUN_VMS.bat from the extracted folder.
goto :fail
:folder_ok
if /i "%PROCESSOR_ARCHITECTURE%"=="AMD64" goto :arch_ok
if /i "%PROCESSOR_ARCHITEW6432%"=="AMD64" goto :arch_ok
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" goto :arch_ok
echo   [ERROR]     64-bit Windows is required ^(PyTorch has no 32-bit build^).
goto :fail
:arch_ok
echo   [ OK ]      64-bit Windows, project folder complete

REM long-path warning: pip unpacks PyTorch into deep folders
set "LONGPATH=0"
reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled 2>nul | find "0x1" >nul
if not errorlevel 1 set "LONGPATH=1"
if "%LONGPATH%"=="1" goto :path_ok
if "%ROOT:~100,1%"=="" goto :path_ok
echo   [WARNING]   The folder path is very long and Windows long paths are off:
echo               %ROOT%
echo               If the package install fails, move the folder closer to C:\ ^(e.g. C:\cloud-vms^).
:path_ok

REM ---------------------------------------------------------------- 2. Python
REM An existing, working project environment already has a suitable Python.
set "VENV_OK=0"
if not exist ".venv\Scripts\python.exe" goto :venv_checked
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 set "VENV_OK=1"
:venv_checked
if "%VENV_OK%"=="1" (
  echo   [ OK ]      Python environment .venv ^(already set up^)
  goto :python_done
)

call :find_python
if defined BASEPY goto :python_found
echo   [INSTALL]   Python 3.12 ^(no Python 3.10 - 3.12 found^)
REM Newer Windows ships the "Python install manager" as py.exe - it installs runtimes itself.
where py >nul 2>nul
if errorlevel 1 goto :python_no_pymanager
echo               installing with the Python install manager ^(py install 3.12^) ...
set "PYTHON_MANAGER_AUTOMATIC_INSTALL="
py install --yes 3.12
call :find_python
if defined BASEPY goto :python_installed
:python_no_pymanager
if "%HAVE_WINGET%"=="1" (
  echo               installing with winget ...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
)
call :find_python
if defined BASEPY goto :python_installed
echo               downloading the installer from python.org ...
call :download "https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-amd64.exe" "%DL%\python-installer.exe"
if errorlevel 1 goto :python_missing
echo               running the installer ^(per-user, no admin rights needed^) ...
"%DL%\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 SimpleInstall=1
call :find_python
if defined BASEPY goto :python_installed
:python_missing
echo   [ERROR]     Python could not be installed automatically.
echo               Install Python 3.12 from https://www.python.org/downloads/ ^(tick "Add Python to PATH"^)
echo               and run this launcher again.
goto :fail
:python_installed
echo   [ OK ]      Python installed: %BASEPY%
goto :python_done
:python_found
echo   [ OK ]      Python found: %BASEPY%
:python_done

REM ---------------------------------------------------------------- 3. Visual C++ runtime
call :have_vcredist
if not errorlevel 1 (
  echo   [ OK ]      Microsoft Visual C++ runtime
  goto :vc_done
)
echo   [INSTALL]   Microsoft Visual C++ runtime ^(PyTorch needs it; Windows may ask for permission^)
if "%HAVE_WINGET%"=="1" winget install -e --id Microsoft.VCRedist.2015+.x64 --silent --accept-package-agreements --accept-source-agreements
call :have_vcredist
if not errorlevel 1 goto :vc_installed
call :download "https://aka.ms/vs/17/release/vc_redist.x64.exe" "%DL%\vc_redist.x64.exe"
if not errorlevel 1 "%DL%\vc_redist.x64.exe" /install /quiet /norestart
call :have_vcredist
if not errorlevel 1 goto :vc_installed
echo   [WARNING]   Could not confirm the Visual C++ runtime. If PyTorch fails to load, install
echo               https://aka.ms/vs/17/release/vc_redist.x64.exe and run the launcher again.
goto :vc_done
:vc_installed
echo   [ OK ]      Microsoft Visual C++ runtime installed
:vc_done

REM ---------------------------------------------------------------- 4. Google Chrome
call :find_chrome
if defined CHROME (
  echo   [ OK ]      Google Chrome
  goto :chrome_done
)
echo   [INSTALL]   Google Chrome ^(the dashboard opens in a Chrome tab^)
if "%HAVE_WINGET%"=="1" winget install -e --id Google.Chrome --silent --accept-package-agreements --accept-source-agreements
call :find_chrome
if defined CHROME goto :chrome_installed
call :download "https://dl.google.com/chrome/install/latest/chrome_installer.exe" "%DL%\chrome_installer.exe"
if not errorlevel 1 "%DL%\chrome_installer.exe" /silent /install
call :find_chrome
if defined CHROME goto :chrome_installed
echo   [WARNING]   Chrome could not be installed - the dashboard will open in your default browser.
goto :chrome_done
:chrome_installed
echo   [ OK ]      Google Chrome installed
:chrome_done

REM ---------------------------------------------------------------- 5. Node.js (only if needed)
if exist "frontend\dist\index.html" (
  echo   [ OK ]      Node.js not needed ^(web interface is pre-built^)
  goto :node_done
)
call :have_node
if not errorlevel 1 (
  echo   [ OK ]      Node.js
  goto :node_done
)
echo   [INSTALL]   Node.js LTS ^(needed to build the web interface^)
if "%HAVE_WINGET%"=="1" winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
call :have_node
if not errorlevel 1 goto :node_installed
call :download "https://nodejs.org/dist/v22.20.0/node-v22.20.0-x64.msi" "%DL%\node.msi"
if not errorlevel 1 msiexec /i "%DL%\node.msi" /passive /norestart
call :have_node
if not errorlevel 1 goto :node_installed
echo   [ERROR]     Node.js could not be installed. Install it from https://nodejs.org and run again.
goto :fail
:node_installed
echo   [ OK ]      Node.js installed
:node_done

REM ---------------------------------------------------------------- 6. virtual environment
if "%VENV_OK%"=="1" goto :venv_done
if exist ".venv" (
  echo   [INSTALL]   Re-creating the broken project environment .venv
  rmdir /s /q ".venv"
) else (
  echo   [INSTALL]   Project environment .venv
)
%BASEPY% -m venv .venv
if errorlevel 1 goto :venv_fail
if not exist ".venv\Scripts\python.exe" goto :venv_fail
echo   [ OK ]      Project environment .venv created
goto :venv_done
:venv_fail
echo   [ERROR]     Could not create the virtual environment with %BASEPY%
goto :fail
:venv_done

REM ---------------------------------------------------------------- 7. packages, models, data
".venv\Scripts\python.exe" "scripts\check_setup.py"
if errorlevel 1 goto :fail

echo ================= All prerequisites ready =================
echo.
endlocal & set "VMS_CHROME=%CHROME%"
exit /b 0

:fail
echo.
echo   Setup is not complete - read the messages above, fix the problem and run again.
if /i not "%NOPAUSE%"=="nopause" pause
endlocal
exit /b 1


REM =====================================================================
REM  helpers
REM =====================================================================

REM ---- BASEPY = full path (quoted) of a WORKING Python 3.10 - 3.12, empty if none.
REM      Candidates are verified by scripts\probe_python.py writing sys.executable to a
REM      file - exit codes are not trusted, because the new Python install manager
REM      (py.exe) exits with 0 even when the requested version is not installed.
:find_python
set "BASEPY="
if not exist "%DL%" mkdir "%DL%"
set "PROBE_OUT=%DL%\python_probe.txt"
REM never let py.exe start a hidden automatic install while probing
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
where py >nul 2>nul
if errorlevel 1 goto :find_python_path
for %%V in (3.12 3.11 3.10) do call :probe_cmd py -%%V
if defined BASEPY goto :find_python_end
:find_python_path
where python >nul 2>nul
if errorlevel 1 goto :find_python_dirs
call :probe_cmd python
if defined BASEPY goto :find_python_end
:find_python_dirs
for %%V in (312 311 310) do call :probe_file "%LocalAppData%\Programs\Python\Python%%V\python.exe"
for %%V in (3.12 3.11 3.10) do call :probe_file "%LocalAppData%\Python\pythoncore-%%V-64\python.exe"
for %%V in (312 311 310) do call :probe_file "%ProgramFiles%\Python%%V\python.exe"
:find_python_end
set "PYTHON_MANAGER_AUTOMATIC_INSTALL="
exit /b 0

REM probe a command such as "py -3.12" or "python"
:probe_cmd
if defined BASEPY exit /b 0
if exist "%PROBE_OUT%" del /f /q "%PROBE_OUT%" >nul 2>nul
%* "scripts\probe_python.py" "%PROBE_OUT%" >nul 2>nul
goto :probe_read

REM probe a python.exe path
:probe_file
if defined BASEPY exit /b 0
if not exist "%~1" exit /b 0
if exist "%PROBE_OUT%" del /f /q "%PROBE_OUT%" >nul 2>nul
"%~1" "scripts\probe_python.py" "%PROBE_OUT%" >nul 2>nul

:probe_read
set "_EXE="
if exist "%PROBE_OUT%" set /p _EXE=<"%PROBE_OUT%"
if not defined _EXE exit /b 0
if not exist "%_EXE%" exit /b 0
set "BASEPY="%_EXE%""
exit /b 0

REM ---- errorlevel 0 when the VC++ 2015-2022 x64 runtime is installed
:have_vcredist
reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" /v Installed 2>nul | find "0x1" >nul
if not errorlevel 1 exit /b 0
reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" /v Installed 2>nul | find "0x1" >nul
if not errorlevel 1 exit /b 0
exit /b 1

REM ---- CHROME = full path of chrome.exe (empty if not installed)
:find_chrome
set "CHROME="
call :try_chrome "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
call :try_chrome "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
call :try_chrome "%LocalAppData%\Google\Chrome\Application\chrome.exe"
if defined CHROME exit /b 0
for %%K in ("HKCU" "HKLM" "HKLM\SOFTWARE\WOW6432Node") do call :chrome_from_registry %%K
exit /b 0

:chrome_from_registry
if defined CHROME exit /b 0
set "_KEY=%~1\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
if /i "%~1"=="HKLM\SOFTWARE\WOW6432Node" set "_KEY=HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
for /f "tokens=2,*" %%A in ('reg query "%_KEY%" /ve 2^>nul ^| find "REG_"') do call :try_chrome "%%~B"
exit /b 0

:try_chrome
if defined CHROME exit /b 0
if exist "%~1" set "CHROME=%~1"
exit /b 0

REM ---- errorlevel 0 when Node.js 18+ and npm are on PATH (adds the default install folder first)
:have_node
if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"
where npm >nul 2>nul
if errorlevel 1 exit /b 1
node -e "process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)" >nul 2>nul
if errorlevel 1 exit /b 1
exit /b 0

REM ---- download URL to FILE (curl.exe ships with Windows 10+, PowerShell as fallback)
:download
if not exist "%DL%" mkdir "%DL%"
if exist "%~2" del /f /q "%~2" >nul 2>nul
set "DL_URL=%~1"
set "DL_OUT=%~2"
echo               downloading %~1
curl.exe -L -f -s -S --retry 2 -o "%~2" "%~1" 2>nul
if exist "%~2" exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri $env:DL_URL -OutFile $env:DL_OUT" 2>nul
if exist "%~2" exit /b 0
echo               download failed
exit /b 1

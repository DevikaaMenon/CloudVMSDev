@echo off
REM Kept for compatibility - checks prerequisites and installs only what is missing.
REM To set up AND launch in one step, double-click RUN_VMS.bat
call "%~dp0install_prerequisites.bat" nopause
if /i not "%~1"=="nopause" pause

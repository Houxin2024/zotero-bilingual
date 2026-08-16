@echo off
setlocal
chcp 65001 >nul

set "INSTALLER=%~dp0windows\install.ps1"
if not exist "%INSTALLER%" (
  echo [ERROR] windows\install.ps1 was not found.
  echo Please extract the complete Windows bundle before running this file.
  pause
  exit /b 2
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%" %*
set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
  echo.
  echo Installation failed with exit code %RESULT%.
  echo See the message above, then run Install-Windows.cmd again.
) else (
  echo.
  echo Installation finished. You can close this window.
)

if /I not "%BLR_NO_PAUSE%"=="1" pause
exit /b %RESULT%

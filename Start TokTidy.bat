@echo off
rem Starts TokTidy. (The desktop shortcut does the same thing without this window.)
cd /d "%~dp0"
if not exist "app\node_modules\electron\dist\electron.exe" (
  echo TokTidy is not installed yet. Double-click setup.bat first.
  pause
  exit /b 1
)
start "" "app\node_modules\electron\dist\electron.exe" "%~dp0app"

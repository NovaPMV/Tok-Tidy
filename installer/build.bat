@echo off
rem Builds dist\TokTidy-Setup-<version>.exe on Windows. Needs NSIS 3 (https://nsis.sourceforge.io).
setlocal
cd /d "%~dp0"
set "MAKENSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
if not exist "%MAKENSIS%" set "MAKENSIS=%ProgramFiles%\NSIS\makensis.exe"
if not exist "%MAKENSIS%" ( echo NSIS was not found. Install it from https://nsis.sourceforge.io & exit /b 1 )
for /f "delims=" %%v in ('powershell -NoProfile -Command "(Get-Content ..\app\package.json -Raw | ConvertFrom-Json).version"') do set "VERSION=%%v"
if not exist build mkdir build
if not exist ..\dist mkdir ..\dist
if not exist build\rcedit-x64.exe (
  powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe -OutFile build\rcedit-x64.exe"
)
powershell -NoProfile -Command "if ((Get-FileHash build\rcedit-x64.exe).Hash -ne '3E7801DB1A5EDBEC91B49A24A094AAD776CB4515488EA5A4CA2289C400EADE2A') { Write-Host 'rcedit checksum mismatch'; exit 1 }" || exit /b 1
"%MAKENSIS%" /V2 /DVERSION=%VERSION% TokTidy.nsi || exit /b 1
echo Built ..\dist\TokTidy-Setup-%VERSION%.exe

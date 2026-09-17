@echo off
rem Builds TokTidy.exe (the launcher) in the TokTidy folder.
setlocal
cd /d "%~dp0.."
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
  echo [!] The Windows C# compiler was not found, so TokTidy.exe was not built.
  echo     Use "Start TokTidy.bat" instead. Everything else works the same.
  exit /b 1
)
if exist "TokTidy.exe" del /f /q "TokTidy.exe" >nul 2>nul
"%CSC%" /nologo /target:winexe /optimize+ /platform:anycpu ^
  /reference:System.Windows.Forms.dll ^
  /win32icon:"app\assets\icon.ico" ^
  /out:"TokTidy.exe" "tools\launcher\TokTidy.cs"
if errorlevel 1 (
  echo [!] Building TokTidy.exe failed. Use "Start TokTidy.bat" instead.
  exit /b 1
)
echo     Built TokTidy.exe
exit /b 0

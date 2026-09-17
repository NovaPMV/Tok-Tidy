@echo off
rem TokTidy command launcher. Usage: toktidy <command>   (toktidy help for the list)
setlocal
set "TOKTIDY_HOME=%~dp0"
if not exist "%TOKTIDY_HOME%.venv\Scripts\python.exe" (
  echo TokTidy is not installed yet. Run setup.bat first.
  exit /b 1
)
set "PYTHONPATH=%TOKTIDY_HOME%backend"
set "PYTHONUTF8=1"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
"%TOKTIDY_HOME%.venv\Scripts\python.exe" -m toktidy %*
exit /b %errorlevel%

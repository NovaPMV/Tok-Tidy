@echo off
rem TokTidy command line (advanced). Usage: toktidy-cli help
setlocal
set "ENGINE=%~dp0"
set "PYTHONPATH=%ENGINE%backend"
set "PYTHONUTF8=1"
set "HF_HOME=%ENGINE%..\models"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "PATH=%ENGINE%ffmpeg\bin;%PATH%"
set "TOKTIDY_SITE_PACKAGES=%ENGINE%env\Lib\site-packages"
rem Use the real Python (keeps working if the TokTidy folder is moved); skip junction links.
set "BASEPY="
for /f "delims=" %%D in ('dir /b /ad-l "%ENGINE%python\cpython-3*" 2^>nul') do (
  if exist "%ENGINE%python\%%D\python.exe" set "BASEPY=%ENGINE%python\%%D\python.exe"
)
if defined BASEPY (
  "%BASEPY%" -m toktidy._boot toktidy %*
) else (
  "%ENGINE%env\Scripts\python.exe" -m toktidy %*
)
exit /b %errorlevel%

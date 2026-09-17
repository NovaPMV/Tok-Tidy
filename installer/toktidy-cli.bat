@echo off
rem TokTidy command line (advanced). Usage: toktidy-cli help
setlocal
set "ENGINE=%~dp0"
set "PYTHONPATH=%ENGINE%backend"
set "PYTHONUTF8=1"
set "HF_HOME=%ENGINE%..\models"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "PATH=%ENGINE%ffmpeg\bin;%PATH%"
"%ENGINE%env\Scripts\python.exe" -m toktidy %*
exit /b %errorlevel%

@echo off
setlocal
cd /d "%~dp0"
rem PyTorch build for NVIDIA GPUs. If the GPU check fails, change cu128 to cu126 and run setup again.
set "TORCH_INDEX=https://download.pytorch.org/whl/cu128"

echo.
echo  ==================================================
echo    TokTidy setup
echo    This takes 10-30 minutes the first time.
echo    You can run it again any time to repair or update.
echo  ==================================================
echo.

rem ---------------------------------------------------------------- checks
where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python was not found.
  echo     Install Python 3.12 from python.org and tick "Add python.exe to PATH".
  goto :fail
)
python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)"
if errorlevel 1 (
  echo [X] TokTidy needs Python 3.10 to 3.13. You have:
  python --version
  goto :fail
)
python -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
  echo [X] Your Python is the 32-bit version. Install the 64-bit version.
  goto :fail
)
where npm >nul 2>nul
if errorlevel 1 (
  echo [X] Node.js was not found.
  echo     Install the LTS version from nodejs.org, then run setup again.
  goto :fail
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [!] ffmpeg was not found. Setup will continue, but indexing needs it.
  echo     See README step 4.
  echo.
)

rem ---------------------------------------------------------------- python side
if not exist ".venv\Scripts\python.exe" (
  echo [1/6] Creating a private Python environment ...
  python -m venv .venv
  if errorlevel 1 goto :fail
) else (
  echo [1/6] Using the existing Python environment
)
set "PY=%~dp0.venv\Scripts\python.exe"

echo [2/6] Updating pip ...
"%PY%" -m pip install --upgrade pip --quiet
if errorlevel 1 goto :fail

echo [3/6] Installing PyTorch with NVIDIA GPU support - about 3 GB ...
"%PY%" -m pip install torch torchvision --index-url %TORCH_INDEX%
if errorlevel 1 goto :fail

echo [4/6] Installing the other Python libraries ...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------- app side
echo [5/6] Installing the app window - about 250 MB ...
pushd app
call npm install --no-audit --no-fund
set "NPMERR=%errorlevel%"
popd
if not "%NPMERR%"=="0" goto :fail
if not exist "app\node_modules\electron\dist\electron.exe" (
  echo [X] The app window was not installed completely. Run setup again.
  goto :fail
)

echo [6/6] Creating TokTidy.exe and the Desktop shortcut ...
call "%~dp0tools\build-launcher.bat"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make-shortcut.ps1" -Root "%~dp0."

rem ---------------------------------------------------------------- report
echo.
echo Checking your GPU ...
"%PY%" -c "import torch; ok=torch.cuda.is_available(); print('  PyTorch', torch.__version__, '| GPU:', torch.cuda.get_device_name(0) if ok else 'NOT DETECTED'); import sys; sys.exit(0 if ok else 1)"
if errorlevel 1 (
  echo.
  echo [!] PyTorch cannot see your GPU. See README "Troubleshooting".
)
echo.
call "%~dp0tts.bat" doctor
echo.
echo  ==================================================
echo    Done! Open TokTidy from the shortcut on your
echo    Desktop, or TokTidy.exe in this folder.
echo  ==================================================
pause
exit /b 0

:fail
echo.
echo Setup did not finish. Scroll up to the first [X] or error message,
echo or copy this whole window's text into the chat.
pause
exit /b 1

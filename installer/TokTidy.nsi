; TokTidy installer (NSIS 3). Build with installer/build.sh or installer/build.bat.
; Installs per user (no administrator rights) and downloads the heavy parts
; (app window, Python, PyTorch, ffmpeg, AI models) during installation.

Target amd64-unicode
ManifestDPIAware true
SetCompressor /SOLID lzma
RequestExecutionLevel user

!ifndef VERSION
  !define VERSION "0.4.3"
!endif
!define APPNAME "TokTidy"
!define PUBLISHER "TokTidy"
!ifndef REPO_URL
  !define REPO_URL "https://github.com/NovaPMV/TokTidy"
!endif
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\TokTidy"
!define UNINSTEXE "Uninstall TokTidy.exe"

Name "${APPNAME}"
Caption "${APPNAME} ${VERSION} Setup"
UninstallCaption "Uninstall ${APPNAME}"
OutFile "../dist/TokTidy-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\TokTidy"
InstallDirRegKey HKCU "Software\TokTidy" "InstallDir"
ShowInstDetails show
ShowUninstDetails show
BrandingText "${APPNAME} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "FileDescription" "${APPNAME} Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "LegalCopyright" "MIT License"

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "Sections.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

Var PS
Var StepNo
Var StepTotal
Var EngineChoice
Var InstallOK
Var LibPath

; ------------------------------------------------------------------ look
!define MUI_ICON "../app/assets/icon.ico"
!define MUI_UNICON "../app/assets/icon.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "assets/sidebar.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "assets/sidebar.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "assets/header.bmp"
!define MUI_HEADERIMAGE_UNBITMAP "assets/header.bmp"
!define MUI_ABORTWARNING
!define MUI_ABORTWARNING_TEXT "Stop installing TokTidy? You can run the installer again later; finished steps are skipped."
!define MUI_COMPONENTSPAGE_SMALLDESC

; ------------------------------------------------------------------ pages
!define MUI_WELCOMEPAGE_TITLE "Welcome to TokTidy ${VERSION}"
!define MUI_WELCOMEPAGE_TITLE_3LINES
!define MUI_WELCOMEPAGE_TEXT "TokTidy helps video editors find matching clips in large collections of short videos: moving previews, AI search by description, picture, colour or speech, and drag-and-drop into Premiere Pro.$\r$\n$\r$\nDisk space: TokTidy takes up about 13 GB with NVIDIA GPU support, or about 9 GB with the CPU-only version (you choose on a later page). Setup also needs about 4 GB of temporary space while it works.$\r$\n$\r$\nSetup downloads everything TokTidy needs, so keep this PC online. It takes about 15-45 minutes depending on your connection. Everything goes into one folder, and no administrator rights are needed.$\r$\n$\r$\nClick Next to continue."
!insertmacro MUI_PAGE_WELCOME

!define MUI_LICENSEPAGE_TEXT_TOP "TokTidy is free software. Please read the license below."
!insertmacro MUI_PAGE_LICENSE "../LICENSE"

!define MUI_COMPONENTSPAGE_TEXT_TOP "Choose what to install. Hover over an item to see what it is. The space needed updates as you choose."
!define MUI_COMPONENTSPAGE_TEXT_COMPLIST "Select what to install:"
!insertmacro MUI_PAGE_COMPONENTS

!define MUI_DIRECTORYPAGE_TEXT_TOP "TokTidy will be installed in the folder below. Your video library and search data are stored separately; you choose that location when TokTidy first opens."
!insertmacro MUI_PAGE_DIRECTORY

!define MUI_INSTFILESPAGE_FINISHHEADER_TEXT "Installation complete"
!define MUI_INSTFILESPAGE_FINISHHEADER_SUBTEXT "TokTidy is ready to use."
!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_TITLE "TokTidy is installed"
!define MUI_FINISHPAGE_TEXT "TokTidy has been installed on your computer.$\r$\n$\r$\nWhen it opens for the first time, it asks where to keep its library (previews and search data) and which video folders to add.$\r$\n$\r$\nClick Finish to close Setup."
!define MUI_FINISHPAGE_RUN "$INSTDIR\TokTidy.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch TokTidy now"
!define MUI_PAGE_CUSTOMFUNCTION_SHOW FinishShow
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!define MUI_COMPONENTSPAGE_TEXT_TOP "Choose what to remove. Your own video files are never deleted."
!insertmacro MUI_UNPAGE_COMPONENTS
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ------------------------------------------------------------------ helpers
!macro FindPowerShell
  ${If} ${FileExists} "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
    StrCpy $PS "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  ${Else}
    StrCpy $PS "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"
  ${EndIf}
!macroend

!macro CheckAppClosed UN
Function ${UN}CheckAppClosed
  retry:
    nsExec::ExecToStack `"$SYSDIR\cmd.exe" /c tasklist /FI "IMAGENAME eq TokTidy.exe" /NH | "$SYSDIR\find.exe" /I "TokTidy.exe"`
    Pop $0
    Pop $1
    ${If} $0 == 0
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "TokTidy is open. Please close it, then click Retry." /SD IDCANCEL IDRETRY retry
      Abort
    ${EndIf}
FunctionEnd
!macroend
!insertmacro CheckAppClosed ""
!insertmacro CheckAppClosed "un."

; Runs one installer step and stops with a clear message if it fails.
!macro RunStep TITLE STEP EXTRA
  IntOp $StepNo $StepNo + 1
  DetailPrint " "
  DetailPrint "---- Step $StepNo of $StepTotal: ${TITLE} ----"
  nsExec::ExecToLog `"$PS" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\engine\bootstrap.ps1" -Step ${STEP} -Root "$INSTDIR" -AppVersion ${VERSION} ${EXTRA}`
  Pop $0
  ${If} $0 != 0
    DetailPrint "Step failed (code $0)."
    MessageBox MB_OK|MB_ICONSTOP "Setup could not finish this step:$\r$\n${TITLE}$\r$\n$\r$\nThe list of details explains what went wrong. Check your internet connection and run the installer again. Steps that already finished are skipped." /SD IDOK
    Abort
  ${EndIf}
!macroend

; ------------------------------------------------------------------ sections
Section "TokTidy app" SecApp
  SectionIn RO
  AddSize 450000
  Call CheckAppClosed

  Call CountSteps

  DetailPrint "Copying TokTidy files ..."
  SetOutPath "$INSTDIR\resources\app"
  File /r /x node_modules /x package-lock.json "../app/*"
  RMDir /r "$INSTDIR\engine\backend"
  SetOutPath "$INSTDIR\engine\backend"
  File /r /x __pycache__ "../backend/toktidy"
  SetOutPath "$INSTDIR\engine"
  File "../requirements.txt"
  File "bootstrap.ps1"
  File "toktidy-cli.bat"
  SetOutPath "$INSTDIR\engine\tools"
  File "build/rcedit-x64.exe"
  SetOutPath "$INSTDIR"
  File "../LICENSE"
  File "../THIRD_PARTY_NOTICES.md"
  WriteUninstaller "$INSTDIR\${UNINSTEXE}"
  WriteRegStr HKCU "Software\TokTidy" "InstallDir" "$INSTDIR"

  !insertmacro RunStep "Download the app window (Electron, about 115 MB)" "electron" ""
  !insertmacro RunStep "Download the package manager (uv, about 20 MB)" "uv" ""
  !insertmacro RunStep "Set up a private copy of Python" "python" ""
SectionEnd

SectionGroup /e "AI engine (pick one)" SecEngine
  Section "NVIDIA GPU (faster)" SecGPU
    AddSize 5000000
    !insertmacro RunStep "Download the AI engine (PyTorch with GPU support, about 3 GB)" "torch" "-Torch gpu"
  SectionEnd
  Section /o "CPU only" SecCPU
    AddSize 900000
    !insertmacro RunStep "Download the AI engine (PyTorch, CPU-only, about 250 MB)" "torch" "-Torch cpu"
  SectionEnd
SectionGroupEnd

Section "-Libraries" SecLibs
  AddSize 700000
  !insertmacro RunStep "Download the remaining libraries (about 400 MB)" "libs" ""
SectionEnd

Section "Video tools (ffmpeg)" SecFF
  AddSize 300000
  !insertmacro RunStep "Download the video tools (ffmpeg, about 190 MB)" "ffmpeg" ""
SectionEnd

Section "AI models (download now)" SecModels
  AddSize 6800000
  !insertmacro RunStep "Download the AI search and speech models (about 6.7 GB)" "models" ""
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\TokTidy.lnk" "$INSTDIR\TokTidy.exe" "" "$INSTDIR\TokTidy.exe" 0
SectionEnd

Section "-Finish" SecFinish
  !insertmacro RunStep "Finish up and check the installation" "finish" ""
  CreateShortcut "$SMPROGRAMS\TokTidy.lnk" "$INSTDIR\TokTidy.exe" "" "$INSTDIR\TokTidy.exe" 0

  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "TokTidy"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\TokTidy.exe,0"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\${UNINSTEXE}"'
  WriteRegStr HKCU "${UNINSTKEY}" "QuietUninstallString" '"$INSTDIR\${UNINSTEXE}" /S'
  WriteRegStr HKCU "${UNINSTKEY}" "URLInfoAbout" "${REPO_URL}"
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
  DetailPrint "Measuring the installed size (this can take a minute) ..."
  ; GetSize prints a line per folder; keep the details list quiet while it runs.
  SetDetailsPrint none
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  SetDetailsPrint both
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINSTKEY}" "EstimatedSize" "$0"
  StrCpy $InstallOK 1
  DetailPrint " "
  DetailPrint "TokTidy is installed in $INSTDIR"
SectionEnd

; ------------------------------------------------------------------ descriptions
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecApp} "The TokTidy program, its app window, a private copy of Python and the libraries it needs (about 1.2 GB). It does not use or change any Python you already have."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecEngine} "PyTorch runs the AI search models. Pick the GPU version if this PC has an NVIDIA graphics card."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecGPU} "Uses your NVIDIA graphics card, which makes indexing and search many times faster. About 5 GB installed (3 GB download)."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCPU} "Works on any PC but indexing is much slower. Fine for small collections. About 0.9 GB installed."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecFF} "ffmpeg reads your videos and makes the small previews. A private copy (about 300 MB) is recommended even if you already have ffmpeg; untick only if yours is on PATH."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecModels} "OpenCLIP and SigLIP 2 (search by description and picture) and Whisper (speech). About 6.7 GB. If unticked, they download the first time TokTidy needs them."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Adds a TokTidy shortcut to your desktop. A Start menu shortcut is always created."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ------------------------------------------------------------------ install callbacks
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "TokTidy needs 64-bit Windows." /SD IDOK
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "TokTidy needs Windows 10 or newer." /SD IDOK
    Abort
  ${EndIf}
  !insertmacro FindPowerShell
  StrCpy $InstallOK 0

  ; Pick the AI engine that fits this PC.
  StrCpy $EngineChoice ${SecGPU}
  nsExec::ExecToStack `"$PS" -NoProfile -NonInteractive -Command "if (Get-CimInstance Win32_VideoController | Where-Object { $$_.Name -match 'NVIDIA' }) { 'NVIDIA' } else { 'NONE' }"`
  Pop $0
  Pop $1
  StrCpy $2 $1 6
  ${If} $2 != "NVIDIA"
    !insertmacro UnselectSection ${SecGPU}
    !insertmacro SelectSection ${SecCPU}
    StrCpy $EngineChoice ${SecCPU}
  ${EndIf}
FunctionEnd

Function .onSelChange
  !insertmacro StartRadioButtons $EngineChoice
    !insertmacro RadioButton ${SecGPU}
    !insertmacro RadioButton ${SecCPU}
  !insertmacro EndRadioButtons
FunctionEnd

Function .onVerifyInstDir
  ; Refuse drive roots and folders with characters that break Python tools.
  StrLen $0 "$INSTDIR"
  ${If} $0 < 4
    Abort
  ${EndIf}
FunctionEnd

Function CountSteps
  StrCpy $StepNo 0
  StrCpy $StepTotal 6          ; app window, uv, Python, AI engine, libraries, finishing
  ${If} ${SectionIsSelected} ${SecFF}
    IntOp $StepTotal $StepTotal + 1
  ${EndIf}
  ${If} ${SectionIsSelected} ${SecModels}
    IntOp $StepTotal $StepTotal + 1
  ${EndIf}
FunctionEnd

Function FinishShow
  ${If} $InstallOK != 1
    ShowWindow $mui.FinishPage.Run 0
  ${EndIf}
FunctionEnd

; ------------------------------------------------------------------ uninstaller
Section "un.TokTidy program" UnSecApp
  SectionIn RO
  Call un.CheckAppClosed
  Delete "$DESKTOP\TokTidy.lnk"
  Delete "$SMPROGRAMS\TokTidy.lnk"
  ${If} ${FileExists} "$INSTDIR\engine\bootstrap.ps1"
    DetailPrint "Removing $INSTDIR (this can take a minute) ..."
    RMDir /r "$INSTDIR"
  ${Else}
    ; Not a recognisable TokTidy folder: remove only what the installer adds.
    Delete "$INSTDIR\${UNINSTEXE}"
    Delete "$INSTDIR\TokTidy.exe"
    RMDir /r "$INSTDIR\resources"
    RMDir /r "$INSTDIR\engine"
    RMDir "$INSTDIR"
  ${EndIf}
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "Software\TokTidy"
SectionEnd

Section /o "un.My library data" UnSecLibrary
  DetailPrint "Removing the TokTidy library ..."
  nsExec::ExecToLog `"$PS" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\bootstrap.ps1" -Step remove-library -Root "$PLUGINSDIR"`
  Pop $0
SectionEnd

Section /o "un.My settings" UnSecSettings
  RMDir /r "$APPDATA\TokTidy"
SectionEnd

!insertmacro MUI_UNFUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${UnSecApp} "The TokTidy program, its Python copy, the AI engine and the downloaded AI models."
  !insertmacro MUI_DESCRIPTION_TEXT ${UnSecLibrary} "Deletes the previews, frames and search data TokTidy made (library: $LibPath). Your videos are not touched. Leave unticked if you plan to reinstall."
  !insertmacro MUI_DESCRIPTION_TEXT ${UnSecSettings} "Deletes TokTidy's settings (folder list location, preferences) and log files."
!insertmacro MUI_UNFUNCTION_DESCRIPTION_END

Function un.onInit
  !insertmacro FindPowerShell
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "bootstrap.ps1"
  nsExec::ExecToStack `"$PS" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\bootstrap.ps1" -Step library-path -Root "$PLUGINSDIR"`
  Pop $0
  Pop $LibPath
  ; drop the trailing line break
  StrCpy $1 $LibPath 1 -1
  ${If} $1 == "$\n"
    StrCpy $LibPath $LibPath -1
  ${EndIf}
  StrCpy $1 $LibPath 1 -1
  ${If} $1 == "$\r"
    StrCpy $LibPath $LibPath -1
  ${EndIf}
FunctionEnd

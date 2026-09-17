# Creates the "TokTidy" shortcut on the Desktop.
# It points at TokTidy.exe in the program folder when that exists
# (the app also repairs this shortcut by itself if the folder is moved).
param([string]$Root)
$Root = (Resolve-Path $Root).Path
$launcher = Join-Path $Root 'TokTidy.exe'
$electron = Join-Path $Root 'app\node_modules\electron\dist\electron.exe'
$app = Join-Path $Root 'app'
$icon = Join-Path $app 'assets\icon.ico'
if (-not (Test-Path $electron)) { Write-Host "Electron not found at $electron"; exit 1 }
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$link = $shell.CreateShortcut((Join-Path $desktop 'TokTidy.lnk'))
if (Test-Path $launcher) {
    $link.TargetPath = $launcher
    $link.Arguments = ''
    $link.IconLocation = "$launcher,0"
} else {
    $link.TargetPath = $electron
    $link.Arguments = '"' + $app + '"'
    $link.IconLocation = $icon
}
$link.WorkingDirectory = $Root
$link.Description = 'TokTidy'
$link.Save()
# The old in-folder shortcut broke whenever the folder moved; the .exe replaces it.
$old = Join-Path $Root 'TokTidy.lnk'
if ((Test-Path $launcher) -and (Test-Path $old)) { Remove-Item $old -Force }
Write-Host "Desktop shortcut created."

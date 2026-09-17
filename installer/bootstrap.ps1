# TokTidy setup steps. The installer runs this once per step and shows the output live.
#   bootstrap.ps1 -Step <electron|uv|python|torch|libs|ffmpeg|models|finish|library-path|remove-library> -Root <install folder>
# Written for Windows PowerShell 5.1 (built into Windows 10/11). Messages are plain ASCII on purpose.
param(
    [Parameter(Mandatory = $true)][string]$Step,
    [Parameter(Mandatory = $true)][string]$Root,
    [ValidateSet('gpu', 'cpu')][string]$Torch = 'gpu',
    [string]$Models = 'siglip2,openclip,whisper',
    [string]$AppVersion = '0.0.0'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---------------------------------------------------------------- pinned versions
$ElectronVersion = '38.8.6'
$ElectronSha256  = '366ae2b4aa9e6bc89b98c8b5831d46303e45cd49b2970c2b3921de5f2fcfc2e1'
$UvVersion       = '0.12.15'
$PythonVersion   = '3.12'
$TorchVersion    = '2.11.0'
$TorchIndex = @{
    gpu      = 'https://download.pytorch.org/whl/cu128'
    gpu_alt  = 'https://download.pytorch.org/whl/cu126'
    cpu      = 'https://download.pytorch.org/whl/cpu'
}

$Root    = [IO.Path]::GetFullPath($Root)
$Engine  = Join-Path $Root 'engine'
$Tmp     = Join-Path $Engine 'tmp'
$State   = Join-Path $Engine 'state'
$UvExe   = Join-Path $Engine 'uv\uv.exe'
$EnvDir  = Join-Path $Engine 'env'
$PyExe   = Join-Path $EnvDir 'Scripts\python.exe'
$Models_ = Join-Path $Root 'models'

function Say([string]$Text) { [Console]::Out.WriteLine($Text); [Console]::Out.Flush() }
function Fail([string]$Text) { Say ''; Say "ERROR: $Text"; exit 1 }
function Ensure-Dir([string]$Path) { if (-not (Test-Path $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null } }
function Mark-Done([string]$Name) { Ensure-Dir $State; Set-Content -Path (Join-Path $State "$Name.done") -Value (Get-Date -Format s) }
function Is-Done([string]$Name) { Test-Path (Join-Path $State "$Name.done") }
function Fmt-MB([double]$Bytes) { '{0:N0} MB' -f ($Bytes / 1MB) }

function Folder-Bytes([string]$Path) {
    if (-not (Test-Path $Path)) { return 0 }
    $sum = 0
    Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object { $sum += $_.Length }
    return $sum
}

# Download with a progress line every few seconds and up to 3 attempts.
function Get-File([string]$Url, [string]$Dest, [string]$Label) {
    Ensure-Dir (Split-Path $Dest)
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            $req = [System.Net.HttpWebRequest]::Create($Url)
            $req.UserAgent = 'TokTidy-Setup'
            $req.Timeout = 60000
            $req.ReadWriteTimeout = 300000
            $resp = $req.GetResponse()
            $total = $resp.ContentLength
            $in = $resp.GetResponseStream()
            $out = [IO.File]::Create($Dest)
            try {
                $buf = New-Object byte[] 1048576
                $done = 0
                $sw = [Diagnostics.Stopwatch]::StartNew()
                $nextReport = 0
                while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                    $out.Write($buf, 0, $n)
                    $done += $n
                    if ($sw.ElapsedMilliseconds -ge $nextReport) {
                        $nextReport = $sw.ElapsedMilliseconds + 4000
                        if ($total -gt 0) {
                            $pct = [int](100 * $done / $total)
                            Say ("    {0}: {1} of {2} ({3}%)" -f $Label, (Fmt-MB $done), (Fmt-MB $total), $pct)
                        } else {
                            Say ("    {0}: {1}" -f $Label, (Fmt-MB $done))
                        }
                    }
                }
            } finally {
                $out.Close(); $in.Close(); $resp.Close()
            }
            Say ("    {0}: done ({1})" -f $Label, (Fmt-MB $done))
            return
        } catch {
            Say "    Download problem (attempt $attempt of 3): $($_.Exception.Message)"
            if ($attempt -eq 3) { Fail "Could not download $Label. Check your internet connection and run the installer again. Finished steps will be skipped." }
            Start-Sleep -Seconds (5 * $attempt)
        }
    }
}

function Get-Text([string]$Url) {
    $wc = New-Object System.Net.WebClient
    $wc.Headers.Add('User-Agent', 'TokTidy-Setup')
    return $wc.DownloadString($Url)
}

function Check-Sha256([string]$File, [string]$Expected, [string]$Label) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $File).Hash.ToLower()
    if ($actual -ne $Expected.ToLower().Trim()) {
        Remove-Item -Force -LiteralPath $File -ErrorAction SilentlyContinue
        Fail "The $Label download was damaged or changed (checksum mismatch). Please run the installer again."
    }
    Say "    Checksum OK"
}

function Expand-Zip([string]$Zip, [string]$Dest) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path $Dest) { Remove-Item -Recurse -Force -LiteralPath $Dest }
    [IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Dest)
}

# Runs a program, shows its output live, and (optionally) reports how much a
# folder has grown while the program is quiet (useful for big pip downloads).
function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$WatchDir = '', [string]$WatchLabel = 'downloaded') {
    $line = '"' + $Exe + '"'
    foreach ($a in $Arguments) {
        if ($a -match '[\s&|<>^()]') { $line += ' "' + $a + '"' } else { $line += ' ' + $a }
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $psi.Arguments = '/d /s /c "' + $line + ' 2>&1"'
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $base = 0
    if ($WatchDir) { $base = Folder-Bytes $WatchDir }
    $lastShown = -1
    $quiet = [Diagnostics.Stopwatch]::StartNew()
    $task = $p.StandardOutput.ReadLineAsync()
    while ($true) {
        if ($task.Wait(5000)) {
            $l = $task.Result
            if ($null -eq $l) { break }
            if ($l.Trim()) { Say ('    ' + $l) }
            $task = $p.StandardOutput.ReadLineAsync()
        } elseif ($WatchDir -and $quiet.ElapsedMilliseconds -ge 15000) {
            $quiet.Restart()
            $mb = [int](((Folder-Bytes $WatchDir) - $base) / 1MB)
            if ($mb -ne $lastShown -and $mb -gt 1) { $lastShown = $mb; Say ("    ... {0:N0} MB {1} so far" -f $mb, $WatchLabel) }
            else { Say '    ... still working' }
        }
    }
    $p.WaitForExit()
    return $p.ExitCode
}

function Use-UvEnvironment {
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Engine 'python'
    $env:UV_CACHE_DIR = Join-Path $Tmp 'uv-cache'
    $env:UV_PYTHON_PREFERENCE = 'only-managed'   # never touch a Python the user already has
    $env:UV_NO_PROGRESS = '1'
    $env:UV_LINK_MODE = 'copy'
    $env:UV_HTTP_TIMEOUT = '300'
    Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
}

function Use-EngineEnvironment {
    $env:PYTHONPATH = Join-Path $Engine 'backend'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONUNBUFFERED = '1'
    $env:HF_HOME = $Models_
    $env:TORCH_HOME = Join-Path $Models_ 'torch'
    $env:HF_HUB_DISABLE_PROGRESS_BARS = '1'
    $env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
    $env:PATH = (Join-Path $Engine 'ffmpeg\bin') + ';' + $env:PATH
}

# ---------------------------------------------------------------- steps
function Step-Electron {
    $exe = Join-Path $Root 'TokTidy.exe'
    if ((Is-Done "electron-$ElectronVersion") -and (Test-Path $exe)) { Say '  Already installed, skipping.'; return }
    $zip = Join-Path $Tmp "electron-$ElectronVersion.zip"
    Get-File "https://github.com/electron/electron/releases/download/v$ElectronVersion/electron-v$ElectronVersion-win32-x64.zip" $zip 'App window'
    Check-Sha256 $zip $ElectronSha256 'app window'
    $x = Join-Path $Tmp 'electron'
    Say '  Unpacking ...'
    Expand-Zip $zip $x
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $x 'resources\default_app.asar')
    $rc = Invoke-Native (Join-Path $env:SystemRoot 'System32\robocopy.exe') @($x, $Root, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:2', '/W:2')
    if ($rc -ge 8) { Fail 'The app window files could not be copied. Is TokTidy still open?' }
    if (Test-Path $exe) { Remove-Item -Force -LiteralPath $exe }
    Move-Item -LiteralPath (Join-Path $Root 'electron.exe') -Destination $exe
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Root 'LICENSES.chromium.html')
    $rcedit = Join-Path $Engine 'tools\rcedit-x64.exe'
    $icon = Join-Path $Root 'resources\app\assets\icon.ico'
    if ((Test-Path $rcedit) -and (Test-Path $icon)) {
        Say '  Applying the TokTidy icon and name ...'
        $code = Invoke-Native $rcedit @($exe, '--set-icon', $icon,
            '--set-version-string', 'ProductName', 'TokTidy',
            '--set-version-string', 'FileDescription', 'TokTidy',
            '--set-version-string', 'CompanyName', 'TokTidy',
            '--set-version-string', 'InternalName', 'TokTidy',
            '--set-version-string', 'OriginalFilename', 'TokTidy.exe',
            '--set-product-version', $AppVersion)
        if ($code -ne 0) { Say '  (Could not change the icon; TokTidy will still work.)' }
    }
    Remove-Item -Recurse -Force -LiteralPath $x, $zip -ErrorAction SilentlyContinue
    Mark-Done "electron-$ElectronVersion"
    Say '  App window ready.'
}

function Step-Uv {
    if ((Is-Done "uv-$UvVersion") -and (Test-Path $UvExe)) { Say '  Already installed, skipping.'; return }
    $name = 'uv-x86_64-pc-windows-msvc.zip'
    $base = "https://github.com/astral-sh/uv/releases/download/$UvVersion"
    $zip = Join-Path $Tmp $name
    Get-File "$base/$name" $zip 'Package manager'
    try {
        $expected = ((Get-Text "$base/$name.sha256").Trim() -split '\s+')[0]
        Check-Sha256 $zip $expected 'package manager'
    } catch {
        if ($_.Exception.Message -like '*checksum mismatch*') { throw }
        Say "    (Checksum file not available: $($_.Exception.Message))"
    }
    $x = Join-Path $Tmp 'uv'
    Expand-Zip $zip $x
    Ensure-Dir (Split-Path $UvExe)
    Copy-Item -Force (Join-Path $x 'uv.exe') $UvExe
    Remove-Item -Recurse -Force -LiteralPath $x, $zip -ErrorAction SilentlyContinue
    Mark-Done "uv-$UvVersion"
    Say '  Package manager ready.'
}

function Step-Python {
    Use-UvEnvironment
    if (Test-Path $PyExe) {
        $code = Invoke-Native $PyExe @('-c', 'import sys; print(sys.version)')
        if ($code -eq 0) { Say '  Python environment already exists, skipping.'; return }
        Say '  The existing Python environment is broken; recreating it ...'
        Remove-Item -Recurse -Force -LiteralPath $EnvDir
    }
    Say "  Downloading Python $PythonVersion (private copy, about 30 MB) ..."
    $code = Invoke-Native $UvExe @('python', 'install', $PythonVersion)
    if ($code -ne 0) { Fail 'Python could not be downloaded.' }
    $code = Invoke-Native $UvExe @('venv', $EnvDir, '--python', $PythonVersion)
    if ($code -ne 0) { Fail 'The Python environment could not be created.' }
    Say '  Python ready.'
}

function Install-Torch([string]$Index, [bool]$Reinstall) {
    $args_ = @('pip', 'install', '--python', $PyExe, "torch==$TorchVersion", 'torchvision', '--index-url', $Index)
    if ($Reinstall) { $args_ += @('--reinstall-package', 'torch', '--reinstall-package', 'torchvision') }
    return (Invoke-Native $UvExe $args_ (Join-Path $Tmp 'uv-cache') 'downloaded')
}

function Step-Torch {
    Use-UvEnvironment
    Use-EngineEnvironment
    $marker = "torch-$TorchVersion-$Torch"
    if (Is-Done $marker) { Say '  Already installed, skipping.'; return }
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $State 'torch-*.done')
    if ($Torch -eq 'gpu') {
        Say '  Downloading PyTorch with NVIDIA GPU support. This is about 3 GB and can take'
        Say '  10-30 minutes. Progress is shown every 15 seconds.'
        $code = Install-Torch $TorchIndex.gpu $true
        if ($code -ne 0) { Fail 'PyTorch could not be installed.' }
        Say '  Testing the GPU ...'
        $code = Invoke-Native $PyExe @('-m', 'toktidy.bootstrap', 'gpu-check')
        if ($code -eq 3) {
            Say '  Trying a PyTorch build that supports older NVIDIA cards ...'
            $alt = Install-Torch $TorchIndex.gpu_alt $true
            if ($alt -eq 0) { $code = Invoke-Native $PyExe @('-m', 'toktidy.bootstrap', 'gpu-check') }
            if (($alt -eq 0) -and ($code -ne 0)) {
                Say '  Restoring the standard GPU build ...'
                Install-Torch $TorchIndex.gpu $true | Out-Null
            }
        }
        if ($code -ne 0) {
            Say ''
            Say '  NOTE: PyTorch cannot use your graphics card right now. TokTidy will still work'
            Say '  using the processor (slower). Updating your NVIDIA driver and restarting usually fixes this.'
        }
    } else {
        Say '  Downloading PyTorch, CPU-only version (about 250 MB) ...'
        $code = Install-Torch $TorchIndex.cpu $true
        if ($code -ne 0) { Fail 'PyTorch could not be installed.' }
    }
    Remove-Item -Recurse -Force -LiteralPath (Join-Path $Tmp 'uv-cache') -ErrorAction SilentlyContinue
    Mark-Done $marker
    Say '  AI engine ready.'
}

function Step-Libs {
    Use-UvEnvironment
    Say '  Installing the remaining libraries (about 400 MB) ...'
    $req = Join-Path $Engine 'requirements.txt'
    $code = Invoke-Native $UvExe @('pip', 'install', '--python', $PyExe, '-r', $req) (Join-Path $Tmp 'uv-cache') 'downloaded'
    if ($code -ne 0) { Fail 'The Python libraries could not be installed.' }
    Remove-Item -Recurse -Force -LiteralPath (Join-Path $Tmp 'uv-cache') -ErrorAction SilentlyContinue
    Mark-Done 'libs'
    Say '  Libraries ready.'
}

function Step-Ffmpeg {
    $bin = Join-Path $Engine 'ffmpeg\bin'
    if ((Test-Path (Join-Path $bin 'ffmpeg.exe')) -and (Test-Path (Join-Path $bin 'ffprobe.exe'))) { Say '  Already installed, skipping.'; return }
    Say '  Looking up the latest ffmpeg release ...'
    $api = 'https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest'
    try {
        $rel = (Get-Text $api) | ConvertFrom-Json
    } catch {
        # GitHub's API can refuse requests for a while (rate limit); fall back to the fixed "latest" names.
        Say "    (Release list unavailable: $($_.Exception.Message). Using the default download.)"
        $dl = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest'
        $rel = [pscustomobject]@{ assets = @(
            [pscustomobject]@{ name = 'ffmpeg-master-latest-win64-gpl.zip'; browser_download_url = "$dl/ffmpeg-master-latest-win64-gpl.zip" },
            [pscustomobject]@{ name = 'checksums.sha256'; browser_download_url = "$dl/checksums.sha256" }
        ) }
    }
    $best = $null; $bestVer = [version]'0.0'
    foreach ($a in $rel.assets) {
        if ($a.name -match '^ffmpeg-n(\d+)\.(\d+)-latest-win64-gpl-\d+\.\d+\.zip$') {
            $v = [version]("{0}.{1}" -f $Matches[1], $Matches[2])
            if ($v -gt $bestVer) { $bestVer = $v; $best = $a }
        }
    }
    if (-not $best) { $best = $rel.assets | Where-Object { $_.name -eq 'ffmpeg-master-latest-win64-gpl.zip' } | Select-Object -First 1 }
    if (-not $best) { Fail 'No suitable ffmpeg download was found.' }
    Say "  Using $($best.name)"
    $zip = Join-Path $Tmp $best.name
    Get-File $best.browser_download_url $zip 'Video tools'
    $sums = $rel.assets | Where-Object { $_.name -eq 'checksums.sha256' } | Select-Object -First 1
    if ($sums) {
        $line = (Get-Text $sums.browser_download_url) -split "`n" | Where-Object { $_ -match [regex]::Escape($best.name) } | Select-Object -First 1
        if ($line) { Check-Sha256 $zip (($line.Trim() -split '\s+')[0]) 'video tools' }
    }
    $x = Join-Path $Tmp 'ffmpeg'
    Say '  Unpacking ...'
    Expand-Zip $zip $x
    $found = Get-ChildItem -LiteralPath $x -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
    if (-not $found) { Fail 'The ffmpeg download did not contain ffmpeg.exe.' }
    Ensure-Dir $bin
    Copy-Item -Force (Join-Path $found.DirectoryName 'ffmpeg.exe') $bin
    Copy-Item -Force (Join-Path $found.DirectoryName 'ffprobe.exe') $bin
    $lic = Get-ChildItem -LiteralPath $x -Recurse -Filter 'LICENSE*' | Select-Object -First 1
    if ($lic) { Copy-Item -Force $lic.FullName (Join-Path (Split-Path $bin) 'LICENSE.txt') }
    Remove-Item -Recurse -Force -LiteralPath $x, $zip -ErrorAction SilentlyContinue
    Say '  Video tools ready.'
}

function Step-Models {
    Use-EngineEnvironment
    Ensure-Dir $Models_
    $code = Invoke-Native $PyExe @('-m', 'toktidy.bootstrap', 'download-models', '--models', $Models)
    if ($code -ne 0) {
        Say ''
        Say '  Some models were not downloaded now. TokTidy downloads them automatically'
        Say '  the first time they are needed, so setup can continue.'
    }
}

function Step-Finish {
    Use-EngineEnvironment
    Say '  Cleaning up temporary files ...'
    Remove-Item -Recurse -Force -LiteralPath $Tmp -ErrorAction SilentlyContinue
    $info = @{ version = $AppVersion; torch = $Torch; installed = (Get-Date -Format s) } | ConvertTo-Json
    Set-Content -Path (Join-Path $Engine 'install.json') -Value $info
    Say '  Checking the installation ...'
    Invoke-Native $PyExe @('-m', 'toktidy', 'doctor') | Out-Null
    Say '  Done.'
}

# Used by the uninstaller -----------------------------------------------------
function Get-LibraryParts {
    $settings = Join-Path $env:APPDATA 'TokTidy\settings.json'
    if (-not (Test-Path $settings)) { return @() }
    try { $s = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json } catch { return @() }
    $parts = @()
    foreach ($k in 'frames', 'previews', 'embeddings') {
        $p = $s.paths.$k
        if ($p -and (Test-Path -LiteralPath $p)) { $parts += $p }
    }
    if ($s.paths.database) { $parts += $s.paths.database }
    return $parts
}

function Step-LibraryPath {
    $db = @(Get-LibraryParts | Where-Object { $_ -like '*.db' })
    if ($db.Count -gt 0) { Say (Split-Path $db[0]) } else { Say '(none found)' }
}

function Step-RemoveLibrary {
    $ids = @('.toktidy-id.json')
    foreach ($p in Get-LibraryParts) {
        if ($p -like '*.db') {
            $dir = Split-Path $p
            $isOurs = $false
            foreach ($i in $ids) { if (Test-Path (Join-Path $dir $i)) { $isOurs = $true } }
            if (-not $isOurs) { Say "  Skipped $p (not recognised as a TokTidy library)"; continue }
            foreach ($f in @($p, "$p-wal", "$p-shm", (Join-Path $dir 'indexing.lock'))) {
                Remove-Item -Force -LiteralPath $f -ErrorAction SilentlyContinue
            }
            Remove-Item -Recurse -Force -LiteralPath (Join-Path $dir 'reports') -ErrorAction SilentlyContinue
            foreach ($i in $ids) { Remove-Item -Force -LiteralPath (Join-Path $dir $i) -ErrorAction SilentlyContinue }
            Say "  Removed the library database in $dir"
            if (-not (Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue)) { Remove-Item -Force -LiteralPath $dir }
        } else {
            $isOurs = $false
            foreach ($i in $ids) { if (Test-Path (Join-Path $p $i)) { $isOurs = $true } }
            if (-not $isOurs) { Say "  Skipped $p (not recognised as a TokTidy folder)"; continue }
            Say "  Removing $p ..."
            Remove-Item -Recurse -Force -LiteralPath $p -ErrorAction SilentlyContinue
            $parent = Split-Path $p
            if ((Test-Path $parent) -and -not (Get-ChildItem -LiteralPath $parent -Force -ErrorAction SilentlyContinue)) {
                Remove-Item -Force -LiteralPath $parent -ErrorAction SilentlyContinue
            }
        }
    }
}

try {
    Ensure-Dir $Engine
    switch ($Step) {
        'electron'       { Step-Electron }
        'uv'             { Step-Uv }
        'python'         { Step-Python }
        'torch'          { Step-Torch }
        'libs'           { Step-Libs }
        'ffmpeg'         { Step-Ffmpeg }
        'models'         { Step-Models }
        'finish'         { Step-Finish }
        'library-path'   { Step-LibraryPath }
        'remove-library' { Step-RemoveLibrary }
        default          { Fail "Unknown step '$Step'" }
    }
    exit 0
} catch {
    Fail $_.Exception.Message
}

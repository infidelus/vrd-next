<#
    One-step setup for VRD Next on Windows 10/11.

    Right-click this file and choose "Run with PowerShell", or from a PowerShell
    window run:

        powershell -ExecutionPolicy Bypass -File install-windows.ps1

    It will, in order:
      1. make sure Python, ffmpeg and mkvmerge are installed, using winget
         (Windows' built-in package manager) for anything missing;
      2. create a virtual environment in the project root (.venv) and install
         the Python dependencies from requirements.txt into it;
      3. create a Start-menu and Desktop shortcut called "VRD Next", using the
         app icon, that launches without a console window.

    It reports each step and pauses at the end, so you can see what happened.
    Re-running it is safe.  Nothing is changed system-wide except the winget
    installs.
#>

# Native commands (pip, winget) print progress to stderr; with "Stop" that would
# abort the whole script.  "Continue" lets it run through and report properly.
$ErrorActionPreference = "Continue"

function Section($t) { Write-Host "`n$t" -ForegroundColor Cyan }
function Info($t)    { Write-Host "  $t" }
function Warn($t)    { Write-Host "  $t" -ForegroundColor Yellow }
function Pause-Exit  { Write-Host ""; Read-Host "Press Enter to close" | Out-Null }

# Resolve paths from the script's own location ($PSScriptRoot is the reliable
# way; fall back to MyInvocation just in case).
$Here = $PSScriptRoot
if (-not $Here) { $Here = Split-Path -Parent $MyInvocation.MyCommand.Path }
$Src  = Split-Path -Parent $Here
$Root = Split-Path -Parent $Src
$Venv = Join-Path $Root ".venv"
$Req  = Join-Path $Root "requirements.txt"
$Icon = Join-Path $Src  "assets\app_icon.ico"
$ProjIcon = Join-Path $Src "assets\project_icon.ico"
Info "Project root: $Root"

# Find a Python that actually runs.  This deliberately ignores the Microsoft
# Store "App execution alias" stubs (zero-byte python.exe / python3.exe in
# WindowsApps): those exist even when Python isn't installed and only open the
# Store, so a plain Get-Command check is misled by them.  The 'py' launcher is
# preferred because the Store stub can't shadow it, and we confirm real Python
# by checking the version output looks like "Python 3.x".
function Get-PythonCmd {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $v = (& py -3 --version 2>&1) -join " "
        if ($v -match "Python \d") { return [pscustomobject]@{ Exe = "py"; Pre = @("-3") } }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $v = (& python --version 2>&1) -join " "
        if ($v -match "Python \d") { return [pscustomobject]@{ Exe = "python"; Pre = @() } }
    }
    return $null
}

function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ";"
}

# --- 1. required tools (via winget) --------------------------------------
Section "1/3  Required tools"
$haveWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)

# Python - check it genuinely runs, not just that a stub exists.
$py = Get-PythonCmd
if ($py) {
    Info "Python found ($($py.Exe) $($py.Pre))."
} elseif ($haveWinget) {
    Info "Python not installed (the Microsoft Store alias doesn't count) - installing via winget..."
    try {
        winget install --id Python.Python.3.14 -e --accept-source-agreements `
            --accept-package-agreements | Out-Null
    } catch { Warn "winget couldn't install Python automatically: $($_.Exception.Message)" }
    Refresh-Path
    $py = Get-PythonCmd
    if ($py) { Info "Python installed ($($py.Exe) $($py.Pre))." }
    else     { Warn "Python still isn't usable - see the note in step 2." }
} else {
    Warn "Python is missing and winget isn't available - install Python from python.org, then re-run."
}

# ffmpeg + mkvmerge - a plain presence check is fine (no Store aliases here).
function Ensure-Tool($cmd, $id, $name) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { Info "$name found."; return }
    if ($haveWinget) {
        Info "Installing $name via winget..."
        try {
            winget install --id $id -e --accept-source-agreements `
                --accept-package-agreements | Out-Null
        } catch { Warn "winget couldn't install $name automatically: $($_.Exception.Message)" }
    } else {
        Warn "$name is missing and winget isn't available - please install it, then re-run."
    }
}
Ensure-Tool "ffmpeg"   "Gyan.FFmpeg"              "ffmpeg"
Ensure-Tool "mkvmerge" "MoritzBunkus.MKVToolNix"  "mkvmerge (MKVToolNix)"
Refresh-Path

# --- 2. virtual environment + dependencies -------------------------------
Section "2/3  Python environment"
if (-not $py) {
    Warn "No usable Python was found.  If typing 'python' opens the Microsoft"
    Warn "Store, turn off the aliases at Settings > Apps > Advanced app settings"
    Warn "> App execution aliases (python.exe and python3.exe), or install Python"
    Warn "from python.org, then run this script again."
    Pause-Exit; return
}
if (-not (Test-Path $Venv)) {
    Info "Creating virtual environment: $Venv"
    $venvArgs = $py.Pre + @("-m", "venv", "$Venv")
    & $py.Exe @venvArgs
} else {
    Info "Reusing existing virtual environment: $Venv"
}
$venvPy  = Join-Path $Venv "Scripts\python.exe"
$venvPyw = Join-Path $Venv "Scripts\pythonw.exe"
if (-not (Test-Path $venvPy)) {
    Warn "The virtual environment wasn't created properly ($venvPy is missing)."
    Warn "Check the messages above and try again."
    Pause-Exit; return
}
# pythonw.exe (windowless) isn't created by every venv/Python build.  If it's
# missing, fall back to python.exe for the shortcut target - a console window
# will flash, but the app launches, which is far better than a dead shortcut
# pointing at a pythonw.exe that doesn't exist.
if (-not (Test-Path $venvPyw)) {
    Warn "pythonw.exe not found in the venv; the shortcut will use python.exe"
    Warn "(a console window will appear briefly when launching)."
    $venvPyw = $venvPy
}
Info "Installing Python dependencies (this can take a minute)..."
& $venvPy -m pip install --upgrade pip

# Install dependencies.  A requirements.txt that's empty or missing (it has
# been seen to extract as 0 bytes) would make "pip install -r" a silent no-op,
# leaving a venv that can't import anything - so treat an empty file as absent
# and use the known package list instead.
$useReq = (Test-Path $Req) -and ((Get-Item $Req).Length -gt 0)
if ($useReq) {
    & $venvPy -m pip install -r "$Req"
} else {
    & $venvPy -m pip install PySide6 av numpy bitstring tqdm
}

# Verify the dependencies actually import.  A reused venv from a previously
# interrupted install can be missing some packages, and pip won't reinstall
# what it thinks is present - so if the check fails, force a clean reinstall
# before creating shortcuts.
& $venvPy -c "import PySide6, av, numpy, bitstring, tqdm" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "Some dependencies are missing or incomplete - reinstalling cleanly..."
    & $venvPy -m pip install --force-reinstall --no-cache-dir PySide6 av numpy bitstring tqdm
}
& $venvPy -c "import PySide6, av, numpy, bitstring, tqdm" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "Python dependencies still aren't importing.  The shortcuts will be"
    Warn "created, but VRD Next may not start until this is resolved - check the"
    Warn "messages above."
}

# --- 3. shortcuts ---------------------------------------------------------
Section "3/3  Shortcuts"
$mainPy = Join-Path $Src "main.py"

function New-AppShortcut($linkPath) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut($linkPath)
        $lnk.TargetPath       = $venvPyw
        $lnk.Arguments        = '"' + $mainPy + '"'
        $lnk.WorkingDirectory = $Src
        if (Test-Path $Icon) { $lnk.IconLocation = $Icon }
        $lnk.Description      = "Frame-accurate cutter for broadcast recordings"
        $lnk.Save()
        if (Test-Path $linkPath) { Info "Created: $linkPath" }
        else { Warn "Save() raised no error but the shortcut isn't there: $linkPath" }
    } catch {
        Warn "Couldn't create $linkPath - $($_.Exception.Message)"
    }
}

$startMenu = Join-Path $env:AppData "Microsoft\Windows\Start Menu\Programs"
$desktop   = [Environment]::GetFolderPath("Desktop")
foreach ($dir in @($startMenu, $desktop)) {
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    New-AppShortcut (Join-Path $dir "VRD Next.lnk")
}

# --- .vprj association (per-user, non-destructive) ------------------------
# Register VRD Next as an available handler for .vprj so it shows up under
# "Open with", WITHOUT making it the default - VideoReDo (or whatever the user
# already has) keeps the default.  All under HKCU, so no admin is needed and
# nothing system-wide changes.  Mirrors what the Linux installer does.
try {
    $progId = "VRDNext.Project"
    $classes = "HKCU:\Software\Classes"

    # Define the ProgID: how to open a .vprj with VRD Next.
    $cmd = "`"$venvPyw`" `"$mainPy`" `"%1`""
    New-Item -Path "$classes\$progId\shell\open\command" -Force | Out-Null
    # The ProgID's (default) is the file-type description (details column).
    Set-ItemProperty -Path "$classes\$progId" -Name "(default)" -Value "VideoReDo Project"
    Set-ItemProperty -Path "$classes\$progId\shell\open\command" -Name "(default)" -Value $cmd
    # FriendlyAppName is what Windows shows in the "Open with" menu.  Without
    # it, Windows reads the command, sees pythonw.exe, and labels the entry
    # "Python" - so set it explicitly on both the ProgID and its open verb
    # (different Windows versions read it from different places).
    Set-ItemProperty -Path "$classes\$progId" -Name "FriendlyAppName" -Value "VRD Next"
    Set-ItemProperty -Path "$classes\$progId\shell\open" -Name "FriendlyAppName" -Value "VRD Next"
    # Use the project-file icon (a document, distinct from the app icon) for
    # .vprj files themselves.  Falls back to the app icon if it's not present.
    $vprjIcon = if (Test-Path $ProjIcon) { $ProjIcon } else { $Icon }
    if (Test-Path $vprjIcon) {
        New-Item -Path "$classes\$progId\DefaultIcon" -Force | Out-Null
        # DefaultIcon wants "path,index" - without the ,0 index some Windows
        # versions fail to resolve a standalone .ico and fall back to the
        # launching interpreter's icon (pythonw.exe's), which is why .vprj
        # files showed the Python icon.
        Set-ItemProperty -Path "$classes\$progId\DefaultIcon" -Name "(default)" -Value "$vprjIcon,0"
    }

    # Add our ProgID to the .vprj extension's "Open with" list, leaving any
    # existing default untouched.
    New-Item -Path "$classes\.vprj\OpenWithProgids" -Force | Out-Null
    Set-ItemProperty -Path "$classes\.vprj\OpenWithProgids" -Name $progId -Value ([byte[]]@()) -Type Binary

    # The "Open with" MENU entry's icon and name come from the application
    # registration, not the ProgID - and because we launch via pythonw.exe,
    # Windows would otherwise show Python's icon and name there.  Registering a
    # named Applications entry with its own FriendlyAppName and DefaultIcon,
    # and listing it in the extension's OpenWithList, gives the menu entry the
    # VRD Next icon and name.
    $appKey = "$classes\Applications\vrd-next.exe"
    New-Item -Path "$appKey\shell\open\command" -Force | Out-Null
    Set-ItemProperty -Path "$appKey" -Name "FriendlyAppName" -Value "VRD Next"
    Set-ItemProperty -Path "$appKey\shell\open\command" -Name "(default)" -Value $cmd
    if (Test-Path $Icon) {
        New-Item -Path "$appKey\DefaultIcon" -Force | Out-Null
        Set-ItemProperty -Path "$appKey\DefaultIcon" -Name "(default)" -Value "$Icon,0"
    }
    # Let .vprj offer this application under "Open with".
    New-Item -Path "$classes\.vprj\OpenWithList\vrd-next.exe" -Force | Out-Null

    Info "Registered VRD Next as an option for .vprj files (not as the default)."
} catch {
    Warn "Couldn't register the .vprj file association - $($_.Exception.Message)"
    Warn "(VRD Next still runs; you can open projects from File > Import Project.)"
}

Section "Done."
Info "Launch VRD Next from the Start menu or the Desktop shortcut."
Info "If ffmpeg/mkvmerge were just installed, a sign-out/in may be needed for"
Info "VRD Next to detect them (Settings > External tools shows their status)."
Pause-Exit

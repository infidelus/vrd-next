# Installing VRD Next

Two one-step installers set everything up (dependencies, a virtual environment,
and menu/desktop shortcuts).  Run the one for your platform.

## Linux (Debian / Ubuntu / Mint)

```sh
cd src/packaging
chmod +x install-linux.sh          # first time only
./install-linux.sh
```

It installs the system packages VRD Next needs (Python, `python3-venv`,
ffmpeg, mkvmerge and the Qt runtime libraries) via apt — asking for sudo only
if something's missing — creates a virtual environment in the project root
(`.venv`) on Python 3.12, installs the Python dependencies into it, adds
**VRD Next** and **VRD Next Watcher** to your applications menu pointing at
that environment, and finally checks the installation imports cleanly.
Re-running it is safe (the venv is reused), and re-running it (or just
`install-desktop-entries.sh`) after moving the project updates the menu
entries' absolute paths.

The Qt runtime libraries matter on a fresh install: without `libxcb-cursor0`
(required by Qt 6.5+) the application starts and then dies without a window,
which is easy to mistake for a broken install.

## Windows 10 / 11

Right-click `install-windows.ps1` and choose **Run with PowerShell**, or:

```powershell
powershell -ExecutionPolicy Bypass -File src\packaging\install-windows.ps1
```

It uses **winget** to install Python, ffmpeg and mkvmerge if they're missing,
creates the `.venv`, installs the Python dependencies, and makes a Start-menu
and Desktop shortcut (using the app icon) that launches without a console
window.  If ffmpeg/mkvmerge were just installed, a sign-out/in may be needed
before VRD Next detects them (see **Settings → External tools**).

> The Windows installer has had basic testing — it installs the dependencies and
> the application launches and runs. Functionality beyond that hasn't been
> exercised much on Windows yet.

## Menu entries only (Linux)

`install-desktop-entries.sh` is the menu-integration step on its own — handy if
you manage the Python environment yourself.  `install-linux.sh` calls it for
you with the venv's interpreter.

```sh
./install-desktop-entries.sh                       # uses python3 from PATH
./install-desktop-entries.sh ../../.venv/bin/python   # or pin the venv's Python
```

It writes two `.desktop` files into `~/.local/share/applications/` with
absolute paths resolved from this checkout, so re-run it if you move the
project.  It also registers the editor as a handler for `.ts` and `.mkv`.

To remove the menu entries:

```sh
rm ~/.local/share/applications/vrd-next.desktop \
   ~/.local/share/applications/vrd-next-watcher.desktop
```

## Icons

`src/assets/app_icon.svg` is the master icon; `app_icon.ico` (multi-resolution,
16–256 px) is generated from it for Windows shortcuts, and `app_icon_256.png`
is a handy raster copy.  Autostarting the **Watcher** on login is handled from
the Watcher's own settings.

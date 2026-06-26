# Linux desktop integration

`install-desktop-entries.sh` adds VRD Next and the VRD Next Watcher to your
applications menu, with the proper icon, for the current user.

```sh
cd src/packaging
chmod +x install-desktop-entries.sh        # first time only
./install-desktop-entries.sh               # uses python3 from PATH
./install-desktop-entries.sh /path/to/venv/bin/python   # or pin an interpreter
```

The script writes two `.desktop` files into `~/.local/share/applications/`
with absolute paths resolved from this checkout, so re-run it if you move the
project.  It also registers the editor as a handler for `.ts` and `.mkv`, so
those show an "Open with VRD Next" entry in the file manager.

To remove the entries:

```sh
rm ~/.local/share/applications/vrd-next.desktop \
   ~/.local/share/applications/vrd-next-watcher.desktop
```

Autostarting the **Watcher** on login is handled separately, from the Watcher's
own settings (it writes `~/.config/autostart/vrd-next-watcher.desktop`), and now
carries the same icon.

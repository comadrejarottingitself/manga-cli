# 0.8.2 requirements and verification map

0.8.2 is a bug-fix patch on the public 0.8.1 baseline. It does not redesign the
reader or source engine.

## Required fixes

- Keep `vo=gpu` first, but do not accept startup merely because IPC and the Lua
  control file appeared. A VO that dies immediately afterwards must be rejected
  so `vo=x11` is tried automatically.
- On Debian 12/Xfce with the default Bash shell, a clean per-user install must make
  `manga-cli` discoverable in newly opened terminals without requiring a manual
  PATH edit. Existing shell content must be preserved and uninstall must remove
  only the exact managed block.
- Preserve saved manga, progress, settings, historical data paths and all Page/Width
  controls from the accepted baseline.

## Verification

- Unit coverage includes a process that dies after apparent mpv startup and one
  that remains alive.
- Installer coverage opens a fresh Bash process using the generated `~/.bashrc`
  and verifies `command -v manga-cli`. Reinstall must not duplicate the block and
  uninstall must preserve unrelated `.bashrc` text.
- The real target check was performed in a clean Debian 12/Xfce VM: `vo=gpu` failed
  under the virtual graphics stack, standalone `vo=x11` worked, and the patched
  reader opened the same chapter automatically via fallback.

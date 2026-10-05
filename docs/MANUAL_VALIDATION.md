# Manual acceptance checklist

Primary target: Debian 12, Xfce/X11, Python 3.11.2 and mpv 0.35.1. For 0.8.3,
also repeat the reader checks on Fedora 44, KDE/Wayland, Python 3.14 and mpv 0.41.
These desktop/live checks must be run by a person; automated mocks do not replace
them. Keep the previously validated package and the installer's private rollback path.

## Interface and settings

1. Install without sudo. On a clean default-Bash target, open a **new terminal**
   and check `command -v manga-cli`, `manga-cli --version` and `manga --version`.
   `command -v manga-cli` should resolve to `~/.local/bin/manga-cli`.
2. Open Search, Saved, History, Settings, Reader options, Sources, Information and
   About. Check English text, unchanged retro layout and no redundant language row.
3. Open Settings -> Appearance. Preview all ten colors. Check titles, borders,
   version, selection and decorations. Enter saves; Esc cancels a later preview.
4. Restart and confirm the saved color. Check success/warning/error colors remain
   green/yellow/red. Try `NO_COLOR=1 manga-cli` as a no-color smoke test.
5. Inspect very long alternative titles/genres, including CJK and combining
   accents, at 80, 100 and 120 columns. Check `...`, aligned borders and no wrapping.
6. Resize the terminal while navigating. Check that a selected appearance entry
   remains visible when moving. The interface redraws on the next input.

## Reader regression

1. Open maximized in the Xfce work area. Verify gpu preference/x11 fallback on the
   target as applicable. If `gpu` dies during startup, confirm the log shows an
   automatic retry with `x11`. Confirm both `f` and `F` (and `v`/`V`) change Page/Width with Caps Lock off/on; F11 is separate.
2. Check left/right click and wheel direction in Page mode.
3. In Width, check scroll, top/bottom transitions, backward entry at the bottom,
   short images, held keys and wheel inertia. Toggle automatic edge turns off/on.
4. Cross both chapter boundaries. Close/reopen and confirm chapter, page, mode
   and vertical position. Try the remember-mode and save-position switches.
5. Check English reader help with I/?, indicator with Tab, and retry messaging.
6. Check prefetch 0/1/3/5/10. At 10, rapidly move forward and backward and verify both directions stay responsive; next-chapter prefetch and wheel/arrow step must still work.
7. On Fedora 44/mpv 0.41, confirm the reader starts without manually patching `reader.py`; inspect `reader.log` only if it fails. On Debian 12/mpv 0.35, confirm the same build still opens normally.

## Sources and rollback

Run `manga-cli --network-test` only when live requests are intended. Exercise both
sources, search and a real reading session. Offline results do not certify current
provider availability.

Run the printed restore command without `--restore-data` and verify that code
returns to the prior version while current reading progress remains. Do not share
private state or unredacted logs in public issues.

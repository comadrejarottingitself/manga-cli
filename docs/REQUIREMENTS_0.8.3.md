# 0.8.3 requirements and verification map

0.8.3 is a narrowly scoped compatibility/performance patch on 0.8.2. It must not
redesign Page/Width navigation, chapter flow, saved progress, source fallback,
installation safety or historical data paths.

## Required changes

- Detect modern mpv support for `--background-color`. When present, launch with
  `--background=color` and `--background-color=#000000`; otherwise retain the
  Debian 12/mpv 0.35-compatible `--background=#000000` syntax.
- Keep `vo=gpu` preferred and the existing output fallback logic unchanged.
- Prefetch pages symmetrically around the current page. The configurable radius
  choices are `0`, `1`, `3`, `5` and `10`; explicit page requests always outrank
  background prefetch and chapter boundaries must never schedule invalid indices.
- Preserve previously saved prefetch values without rewriting user settings.
- Bind both lowercase and uppercase `F`/`V` to Page/Width mode switching so Caps
  Lock does not disable the shortcut. `F11` remains true fullscreen.

## Automated verification

- Unit tests cover legacy and modern mpv command construction.
- Streaming tests cover symmetric windows, the 10-page radius and chapter-edge
  clamping, while existing cache/retry/cancellation tests remain active.
- Lua tests execute uppercase `F` and `V` through the real reader script harness.
- The complete existing installer, privacy, recovery, source and reader suites
  must pass before a candidate archive is built.

## Manual release gates

Before publishing, test the final candidate on the user's real Debian 12 system,
a clean Debian 12/Xfce VM and Fedora 44/KDE/Wayland. Fedora must use the unmodified
0.8.3 package; the earlier local 0.8.2 compatibility patch is evidence for the bug
fix but is not a substitute for final-candidate testing.

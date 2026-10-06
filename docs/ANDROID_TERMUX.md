# Android / Termux profile (experimental)

This branch keeps the public **manga-cli 0.8.3** desktop release intact and adds a separate reader profile for native Termux + Termux:X11 + `mpv-x`.

## Scope

The Android profile deliberately keeps the existing search, source, saved-manga, history, chapter, state, cache and streaming engines. Only the display/input layer changes on Termux.

The target stack is:

```text
manga-cli 0.8.3
  -> Python reader controller
  -> mpv-x
  -> Termux:X11 (:1)
```

Tested prototype assumptions:

- native Termux, not a Debian/Ubuntu PRoot;
- `mpv-x` 0.41-class build;
- Termux:X11 display `:1`;
- X11 video output is the reliable path; GPU/EGL may be unavailable on the device;
- explicit `--geometry=100%x100%+0+0` is required with the stable mpv window used by manga-cli.

## Reader design

Android uses one page-fit mode instead of exposing the desktop Page/Width split.

- tap the **right 35%**: next page;
- tap the **left 35%**: previous page;
- tap the center: page/status overlay;
- double tap: zoom around the touched point;
- double tap again: reset to fit-to-screen;
- drag while zoomed: pan the page;
- rotating/resizing the X11 surface: reset/refit the page to the new viewport;
- `Q` / `Esc`: persist reading progress and leave the reader;
- keyboard/mouse fallbacks remain available for debugging.

The double-tap zoom factor is configurable in Reader settings. Mobile zoom and pan are intentionally transient: page/chapter progress persists, but a phone zoom does not make the desktop reader reopen a title zoomed.

## Why these choices

Established Android readers converge on the same fundamentals: large tap zones, a full-page fit mode, double-tap/pinch zoom, panning, orientation-aware layout, nearby-page preloading, progress resume and fullscreen reading. The Android branch adopts the mechanisms that map cleanly onto mpv/X11 and defers features that need a different rendering architecture (native pinch/multitouch guarantees, tiled/subsampled image decoding, continuous Webtoon composition, automatic border analysis, automatic dual-page spreads).

## Install in Termux

Prerequisites:

```bash
pkg install python x11-repo
pkg install mpv-x termux-x11-nightly
```

Install the branch without using the Debian installer:

```bash
bash termux-install.sh
```

Run:

```bash
manga-termux
```

The installer copies only application code to `~/.local/lib/manga-cli-android-v083` and creates `$PREFIX/bin/manga-termux`. It does not remove or rewrite manga-cli private state/cache.

## Uninstall

```bash
bash termux-uninstall.sh
```

Private reading state is preserved.

## Validation

Run the ordinary project tests plus the Android-specific tests:

```bash
python -m unittest discover -v
python manga.py --self-test
```

The Android-specific suite checks Termux detection, X11-only output selection, fullscreen/window flags, settings normalization, script selection, Lua syntax, and more than 160,000 deterministic randomized gesture/geometry scenarios (tap zones, cursor-centric zoom, cursor-anchor invariance and drag bounds). Physical touch, Android rotation and Termux:X11 compositor behavior still require final validation on a real Android device.

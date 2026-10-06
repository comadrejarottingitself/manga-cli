# Android reader design review for manga-cli 0.8.3

This document records the reader mechanisms reviewed before defining the experimental native-Termux Android profile. It is a design decision record, not a claim that manga-cli implements every feature listed below.

## Readers reviewed

| Reader | Mechanisms observed | Decision for this branch |
| --- | --- | --- |
| Mihon | Paged/strip modes, configurable tap zones, free/locked orientation, fullscreen, fit-screen/width/height/smart-fit, crop borders, wide-page handling, double-tap zoom, volume keys | Adopt tap zones, fit-screen, orientation refit and fullscreen. Defer crop, wide-page composition and volume keys. |
| Open Comic Reader | Side taps, page preloading, progress resume, pinch zoom, double-tap zoom followed by double-tap reset | Adopt side taps, existing manga-cli prefetch/progress, double-tap zoom/reset. |
| Perfect Viewer | Fit modes, pinch/fling, next/previous cache, bookshelf, border crop, single/dual page | Existing manga-cli cache/library cover much of the data model. Defer crop and dual-page composition. |
| CDisplayEx | Swipe pan, pinch, double-tap zoom, double-tap-hold scroll, nine assignable zones, RTL inversion, volume controls | Adopt simple large left/right zones and drag-to-pan. Avoid gesture overload in the first mobile profile. |
| Challenger Comics Viewer | Automatic page loading, vertical/horizontal scrolling, simple/multiple-image modes, fit modes, history and border crop | Keep bounded page prefetch; continuous multi-image mode is a later architectural feature. |
| ComicScreen | Smooth scrolling, one page portrait/two pages landscape, cut margin, hardware-key movement | Orientation-aware refit is adopted. Automatic spread creation and cut-margin analysis are deferred. |
| Kotatsu / Kotatsu-Redo / Kotatsu Next | Standard/Webtoon modes, adjustable zoom/crop/gestures, history/favorites, double-tap zoom anchored to touch with staged zoom levels | Adopt touch-anchored zoom and make its factor configurable. Keep one zoom stage plus reset for predictability. |
| Komikku | Custom page preloading/cache, paged double-tap options, optional Webtoon pinch zoom, adaptive Webtoon scaling | Existing prefetch/cache remain. Webtoon composition and native pinch are deferred. |
| Zoushi | Auto single/spread by orientation, margin trim, double-tap panel zoom, pinch, volume keys | Adopt orientation handling; defer panel/spread/margin logic. |
| Kamigura | Page prefetch, automatic single/spread, pinch/pan/double-tap, tap/swipe turns, page slider | Adopt prefetch, pan, double-tap and taps. Defer spread/sliders. |
| Comic Magic Reader | Edge tap/swipe, pinch/double-tap, multiple fit modes, experimental panel detection | Avoid panel detection in this branch: it is explicitly fragile and changes scope dramatically. |
| Chika | ML panel detection, panel-by-panel navigation, pan/pinch, page scrubber | Interesting future research, not appropriate for the 0.8.3 Android compatibility branch. |

## Final interaction model

The Android profile deliberately has one obvious reading state: **fit page to screen**. Desktop Page/Width modes remain untouched in the stable desktop reader.

- Left 35% single tap: previous page.
- Right 35% single tap: next page.
- Center single tap: status only.
- Double tap: zoom around the point touched.
- Double tap while zoomed: reset to fit screen.
- Drag while zoomed: pan, clamped to the useful viewport.
- Single tap while zoomed: status only, never an accidental page change.
- Screen rotation / X11 viewport resize: discard transient phone zoom and refit the page.
- Reader exit: page/chapter progress persists normally.
- Phone zoom/pan is transient and is not written back as the desktop reading mode.

The default double-tap zoom is 2.0x. Reader settings expose 1.5x / 2.0x / 2.5x because a fixed zoom strength is not comfortable for every scan or phone size.

## Rendering and window policy

The device prototype established a hard compatibility constraint: GPU/EGL is not dependable on the target Termux:X11 stack, while `--vo=x11` is. The Android profile therefore chooses X11 directly instead of paying the GPU failure/fallback cost on every launch.

The Android window keeps the working `--force-window=immediate` and `--auto-window-resize=no` combination and uses explicit `--geometry=100%x100%+0+0`. This is intentional: the desktop `--window-maximized=yes` path reproduced a bad small/soft canvas on the phone, while explicit geometry rendered correctly.

The mobile profile also uses fullscreen, borderless output, black background, aspect preservation, neutral panscan and a short cursor auto-hide interval.

## Zoom and pan model

mpv exposes `mouse-pos`, newer builds expose `touch-pos`, and `video-zoom` uses a base-2 logarithmic scale. The mobile Lua reader prefers touch coordinates when available and falls back to the mouse coordinates delivered by Termux:X11.

Cursor-centric zoom solves the new `video-align-x/y` values so the image point under the pointer remains under that pointer after scaling. Alignment is clamped at the physical edge of the viewport. Dragging uses the same `video-align-x/y` coordinate space, which naturally prevents the image from drifting indefinitely outside the window.

## Deliberately deferred features

These are good reader features, but they do not fit a narrow 0.8.3 Android display/input branch without changing the architecture:

- Native pinch/multitouch as a guaranteed control. Termux:X11 touch delivery must be physically proven first.
- Continuous Webtoon/long-strip composition. manga-cli currently streams one page image into one mpv image view.
- Tile/subsample image decoding. That needs a different image rendering pipeline.
- Automatic white-border crop. That adds image-analysis work before display.
- Automatic two-page spreads. That needs page pairing, composition and manga reading-order rules.
- Panel detection/guided view. Heuristic and ML approaches add a large independent subsystem.
- Volume-key navigation. Android can intercept those keys before X11; device testing is required.
- Swipe-to-turn while unzoomed. It conflicts with the drag gesture and adds little over large tap zones for the first branch.

## Sources reviewed

- Mihon reader guide: https://mihon.app/docs/guides/reader-settings
- Mihon source: https://github.com/mihonapp/mihon
- Open Comic Reader: https://github.com/sketchpunk/opencomicreader
- Perfect Viewer: https://play.google.com/store/apps/details?id=com.rookiestudio.perfectviewer
- CDisplayEx reader: https://www.cdisplayex.com/mobile/reader/
- CDisplayEx settings: https://www.cdisplayex.com/mobile/settings/
- Challenger Comics Viewer: https://play.google.com/store/apps/details?id=org.kill.geek.bdviewer
- ComicScreen: https://play.google.com/store/apps/details?id=com.viewer.comicscreen
- Kotatsu-Redo: https://github.com/Kotatsu-Redo/Kotatsu-Redo
- Kotatsu Next: https://github.com/Ero-gamer/Kotatsu-Next
- Komikku: https://github.com/komikku-app/komikku
- Zoushi: https://play.google.com/store/apps/details?id=jp.opti_inc.mv
- Kamigura: https://github.com/KamiguraApp/Kamigura
- Comic Magic Reader: https://play.google.com/store/apps/details?id=net.kaleidos.comicsmagic
- Chika: https://github.com/batunii/chika
- mpv cursor-centric positioning reference: https://github.com/mpv-player/mpv/blob/master/player/lua/positioning.lua
- mpv option/input documentation: https://mpv.io/manual/master/

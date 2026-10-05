# MANGA-CLI 0.8.3 - release validation

Date: 2026-10-05.

## Scope

0.8.3 intentionally changes only three reader-facing areas:

- mpv background-option compatibility: legacy mpv keeps
  `--background=#000000`; modern mpv builds exposing `--background-color` use
  `--background=color` plus `--background-color=#000000`;
- page prefetch becomes bidirectional, with configurable radii
  `0 / 1 / 3 / 5 / 10` on each side of the current page;
- uppercase `F` and `V` are bound to the same Page/Width toggle as lowercase
  `f` and `v`, while `F11` remains independent fullscreen.

Source adapters, chapter identity/fallback, progress/state storage, historical
paths, installer transactions/recovery and the established Page/Width navigation
semantics are otherwise preserved.

## Automated results

The 0.8.3 candidate contains **429 unittest cases**. The complete suite was run in
segments because of the execution environment's per-command time limit; all 429
discovered cases passed. The application offline self-test reports **55/55 OK**.
Regression coverage includes legacy/modern mpv command lines, bidirectional
prefetch radii and chapter edges, the 10-page radius, uppercase fit-mode keys,
installer/recovery safety, sources, HTTP, state/settings and privacy checks.

The release builder/privacy/integrity gates passed, and two independent release
builds produced byte-identical ZIP archives before the documentation-only
publication refresh recorded below.

## Real Debian 12 validation

The 0.8.3 application/reader code was physically installed and tested on the
maintainer's real Debian 12 + Xfce/X11 machine with Python 3.11.2 and mpv 0.35.1.
The tested candidate passed:

- verified ZIP checksum and normal-user upgrade installation;
- `manga-cli --version` -> `0.8.3`;
- offline self-test -> **55/55 OK**;
- local doctor -> **13/13 OK**;
- end-to-end network test -> **2/2 OK** for MangaKatana and MangaPill;
- live `--source-check "Berserk"` on both providers;
- real chapter reading with Page/Width navigation;
- bidirectional **Prefetch = 10**, including rapid forward/backward navigation;
- uppercase `F` Page/Width switching with Caps Lock behavior fixed.

The publication refresh preserves those tested application/reader bytes. Only
release documentation was updated to retain the latest public compatibility note
and record this validation result; the manifest and ZIP were then rebuilt.

## Fedora 44 / modern mpv evidence

Before 0.8.3 was built, public 0.8.2 was tested on Fedora 44 with KDE/Wayland,
Python 3.14.6 and mpv 0.41.0. Installation and offline checks worked, but mpv
rejected the legacy `--background=#000000` argument. Applying the exact
compatibility behavior now implemented in 0.8.3 (`--background=color` plus
`--background-color=#000000`) made the real reader work normally, including
Page/Width operation.

This is strong diagnostic validation of the 0.8.3 compatibility fix, but the final
unmodified 0.8.3 release archive has not yet been independently rerun on that Fedora
machine. A clean Debian 12 VM rerun of 0.8.3 is also still useful additional
coverage. Debian 12 + Xfce/X11 remains the primary tested target.

## Publication status

0.8.3 is published as a narrowly scoped compatibility/performance update for the
existing Debian 12 target. Additional distro reports are welcome. Future validation
should keep testing clean Debian installations and modern-mpv Wayland systems so
compatibility claims remain evidence-based.

# Changelog

## 0.8.3 - modern mpv compatibility and bidirectional prefetch

- Support both legacy and modern mpv background syntax at runtime. Older mpv builds
  keep `--background=#000000`; builds exposing `--background-color` use
  `--background=color` plus `--background-color=#000000`. This fixes the reader
  startup failure reproduced on Fedora 44/KDE/Wayland with mpv 0.41 without
  dropping Debian 12/mpv 0.35 compatibility.
- Make page prefetch bidirectional around the current page instead of forward-only.
  Prefetch choices are now `0 / 1 / 3 / 5 / 10`, interpreted as the number of
  pages prepared on each side when those pages exist. Existing saved values remain
  valid and are not rewritten by the installer.
- Accept uppercase `F` and `V` as Page/Width toggle keys as well as lowercase
  `f`/`v`, so Caps Lock no longer disables the fit-mode shortcut. `F11` remains
  the separate true-fullscreen control.
- Add regression coverage for legacy/modern mpv command lines, symmetric prefetch
  windows and chapter edges, the 10-page radius, and uppercase fit-mode keys.

## 0.8.2 - Debian 12 clean-install fixes

- Fix the real mpv fallback path discovered in a clean Debian 12/Xfce VM: if
  `vo=gpu` creates IPC/Lua state but then dies while initializing graphics, the
  startup is rejected and MANGA-CLI automatically retries `vo=x11`.
- Keep `vo=gpu` as the preferred output; reader navigation, Page/Width behavior,
  chapter flow, state and provider logic are otherwise unchanged.
- Make the `manga-cli` command available in new default-Bash terminals by adding a
  small marked `~/.bashrc` block after a successful install; uninstall removes
  only that exact managed block.
- Preserve the full-path fallback (`~/.local/bin/manga-cli`) for other shells or
  shell startup files that cannot be updated safely.
- Add regression tests for delayed VO failure and Bash PATH setup.

## 0.8.1 - Final presentation polish

- Fix mpv startup fallback so a delayed `vo=gpu` graphics failure automatically retries with `vo=x11`.
- Simplify the home header from `ONLINE READING · MK + MP` to `ONLINE READING`.
- Remove the redundant visible `C Continue` hint while retaining the shortcut for compatibility.
- Add `Tutorial / guide: README.md` to the About screen.
- Replace the visible project signature with `a comadreja project`.
- Remove the license-status row from About and release the source under the MIT License.
- Keep reader controls, source fallback, chapter navigation, saved state and legacy paths unchanged.
- Harden the public release: automatic Debian/Ubuntu dependency bootstrap for missing `python3`/`mpv`/`ca-certificates`, GitHub Actions CI, issue templates, security policy, reproducible release builder and privacy scanner.
- Refresh the public screenshots from the real 0.8.1 renderers and remove stale 0.8.0/MK+MP imagery.

### Prepublication safety fixes (application version remains 0.8.1)

- Replace destructive release-directory cleanup with scoped atomic output writes.
- Package only an explicit reviewed allowlist; align source, ZIP and staged-Git
  privacy checks, including ignored files force-added to Git and staged-byte drift.
- Require exact checksum coverage and install only the verified payload bytes.
- Replace delete-first install/restore with fsynced staging, private verified
  backups, Linux atomic exchange, a durable recovery journal and process locks.
- Keep a shared lock while a new launcher runs; refuse conflicting setup commands.
- Add recovery without network/mpv, strict path/ownership checks and guarded removal
  of recognized code. Never overwrite reading data during restore.
- Test disk-full/I/O errors, interruptions and real SIGKILL recovery, unsafe paths,
  symlinks, damaged packages/backups, concurrency and reproducible output.
- Retain every accepted application/reader/provider byte; setup tools alone change.

## 0.8.0 - UI and customization

- Translate menus, options, status/error messages, CLI help, reader OSD, installer
  messages and release documentation to English.
- Centralize application messages in `acmanga/locales/en.json`. Generate the
  standalone Lua/bootstrap message tables from that catalogue.
- Adopt MANGA-CLI branding with an optional AntiComadreja signature in About.
- Add Settings -> Appearance: ten accents, live preview, Enter to save, Esc to
  cancel, and Crimson as the backward-compatible default.
- Keep semantic success/warning/error colors independent of the chosen accent.
- Measure framed content by terminal cells, preserve label space and truncate
  long fields with ASCII `...` without wrapping into adjacent columns.
- Remove redundant language rows and English-only badges from the UI, while
  keeping protocol/source language metadata for compatibility.
- Add an About screen and public-repository documentation and privacy checklist.
- Retain historical data/cache locations and the 0.7.5 reader behavior.
- Add automated tests for palette persistence, cancellation, failed writes,
  Unicode widths, rendered screens, localization coverage and reader contracts.
- Ensure a fresh installation can provide the legacy alias when its name is free.

## 0.7.5 - Reader customization baseline

Stable baseline supplied for this release. Page / Width modes, symmetric backward
navigation, anti-bounce behavior, cross-chapter reading, per-manga mode and
position persistence, reader preferences and bounded prefetch were established
and covered by 245 baseline tests.

## Earlier lineage

- 0.7.4: corrected full-page/width view behavior.
- 0.7.3: chapter navigation and video-output/window fixes.
- 0.7.1: compatibility fallback for mpv/Lua JSON encoding on Debian 12.
- 0.7.0: manga-cli branding transition from AntiComadreja Manga.
- 0.6.3: recovered AntiComadreja Manga baseline.

Earlier implementation history is not a claim of new live-source or visual
validation. See VALIDATION.md for the current build's results and limitations.

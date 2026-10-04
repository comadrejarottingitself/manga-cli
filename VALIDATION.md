# MANGA-CLI 0.8.2 - Debian 12 bug-fix validation

Date: 2026-10-04. Scope: the two defects found during the first clean Debian 12
installation test after the 0.8.1 public baseline: delayed mpv video-output failure
and the per-user command PATH in Xfce/Bash.

## Diagnosis from the clean Debian 12 VM

The VM was installed as Debian GNU/Linux 12 (bookworm) with Xfce/X11. Python was
3.11.2. Before MANGA-CLI setup, both `mpv` and `manga-cli` were absent.

After installation, search/chapter preparation reached the reader, but the mpv log
showed that `vo=gpu` created the IPC/Lua state early enough for MANGA-CLI to mark it
ready and then died while the virtual graphics stack was still initializing. The
real log included DRI2 authentication failure, Vulkan/device initialization failure,
CRTC permission errors and finally `Failed initializing any suitable GPU context!`.

`mpv --no-config --vo=x11 --force-window=immediate --idle=yes` opened normally in
the same VM. Temporarily preferring x11 also opened the same manga chapter, proving
that source resolution, page download, mpv itself and the reader protocol were not
the failing components.

The reader fix keeps `gpu` first but requires the process to remain alive briefly
after IPC/control startup. If it dies during that delayed initialization window,
that VO is rejected and the existing candidate loop proceeds to `x11`. The patched
reader then opened the chapter automatically in the same VM without the temporary
x11-first diagnostic change.

A second clean-install issue was also reproduced: `~/.profile` contained Debian's
normal `~/.local/bin` PATH stanza, but new Xfce terminal windows still inherited a
PATH without that directory. Therefore the installed launcher worked by full path
while bare `manga-cli` did not. 0.8.2 adds one marked, app-specific block to
`~/.bashrc` when Bash is the login shell. It is atomic, refuses unsafe startup files,
is not duplicated by reinstall, and uninstall removes only that exact block.

## Automated acceptance checks

All **424 unit/integration tests passed**, executed as a normal non-root user in
module groups so the long failure-injection suites fit the execution limits. This
includes the complete existing safety suites plus new regression coverage for:

- a reader process that appears ready and then dies during VO initialization;
- a stable reader process that remains accepted;
- `gpu` remaining preferred ahead of `x11`;
- creation of the marked Bash PATH block;
- a fresh Bash process resolving `manga-cli` through that block;
- reinstall not duplicating the block; and
- uninstall preserving unrelated `.bashrc` content while removing its own block.

The application offline self-test passes **55/55**. The privacy scanner passes,
generated English/Lua/bootstrap messages are synchronized, `install.sh` and
`uninstall.sh` pass shell syntax checking, and all Python sources parse with the
Python 3.11 grammar.

## Preserved behavior and data

0.8.2 does not redesign Page/Width navigation, mouse/wheel controls, chapter flow,
prefetch, source identity/fallback, progress, settings or historical data paths.
The protected reader/source/state contracts remain covered. Installation and
rollback tests continue to verify that saved manga, progress and settings are not
installation rollback targets.

`vo=gpu` remains the preferred renderer. `vo=x11` is used only when appropriate as
the compatibility fallback. No global mpv configuration is created or modified.

## Release status and remaining manual gate

The mpv fallback correction has been manually confirmed in the clean Debian 12/Xfce
VM. The Bash PATH correction has automated fresh-shell coverage but must still be
reinstalled once in that same VM from the final 0.8.2 candidate and checked with:

```sh
command -v manga-cli
manga-cli --version
```

A final chapter should then be opened again without source edits to reconfirm the
complete installed path: clean Debian 12 -> install -> new terminal -> `manga-cli`
-> chapter -> automatic gpu/x11 selection.

Do not publish 0.8.2 as final until that last candidate-install check and the
GitHub-hosted CI run pass. See `docs/MANUAL_VALIDATION.md` and
`docs/INSTALLATION_SAFETY.md`.

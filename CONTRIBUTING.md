# Contributing

MANGA-CLI is an open-source project released under the MIT License.

## Development environment

Keep runtime code compatible with Python 3.11 and the standard library. The main
target is Debian 12 + Xfce/X11 + mpv 0.35. For the complete test suite on Debian:

```sh
sudo apt install python3 mpv ca-certificates liblua5.4-0 git
```

Run the suite as a normal user, not root, so permission and root-refusal tests
exercise the supported setup path. From the repository root:

```sh
python3 tools/privacy_check.py
python3 tools/sync_text.py --check
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
python3 manga.py --self-test
```

The Lua harness executes the real reader script against a simulated mpv API using
liblua 5.4. A missing Lua library is a test limitation, not a successful reader
validation. Offline tests do not require live MangaKatana/MangaPill access.

## Change boundaries

For 0.8.x, preserve reader controls, scrolling, bounce suppression, chapter order,
source-fallback identity, saved progress, mpv arguments and legacy data paths unless
a separately scoped bug fix requires a change. Behavior changes need focused
regression tests. Do not update a frozen integrity hash merely to hide a change.

Keep English application messages in `acmanga/locales/en.json`. Do not use
translated text as state keys, source IDs, control messages or command names. Run
`python3 tools/sync_text.py` after editing the catalogue and then verify with
`python3 tools/sync_text.py --check`. See `docs/LOCALIZATION.md`.

## Privacy and security

Never commit real reading state, settings, history, progress, cache pages, logs,
backups, credentials, environment files, private URLs, personal home paths or
screenshots containing private information. Run `python3 tools/privacy_check.py`,
but also review `git diff --cached`, `git ls-files` and commit author/email before
publishing. After staging, run `python3 tools/privacy_check.py --git-index` to
check staged paths and bytes rather than only the working copy. A `.gitignore` cannot remove information already committed to history.

Use GitHub private security advisories for vulnerabilities; see `SECURITY.md`.
Ordinary source-site breakage should use the source-breakage issue template.

## Bug reports

Include the application version, OS/desktop, Python/mpv versions, expected versus
actual behavior and a minimal reproducible example. Share sanitized diagnostics
only. Synthetic fixtures belong under `tests/`; real user state does not.

## Release packaging

Use the repository builder rather than creating ZIP files manually:

```sh
python3 tools/build_release.py
```

It verifies privacy/generated-text guardrails, regenerates `SHA256SUMS`, runs the
suite and self-test, and writes the release ZIP plus checksum to `dist/`. Follow
`docs/RELEASE_CHECKLIST.md` before publishing a tag or GitHub Release.

New public files must be explicitly added to `release-files.txt` after review.
Never weaken the allowlist, checksum coverage or negative privacy tests to hide a
local file. See `docs/INSTALLATION_SAFETY.md` for transaction invariants and limits.

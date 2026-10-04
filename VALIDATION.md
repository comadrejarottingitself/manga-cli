# MANGA-CLI 0.8.1 - hardened public release validation

Date: 2026-10-03. Scope: setup, recovery, release safety and regression checks.
Application version remains 0.8.1; no repository was published by these checks.

## Preserved application

All **20 application files** (`manga.py` and the full `acmanga/` payload, including
Python, Lua and the English catalogue) are byte-for-byte identical to the accepted
0.8.1 ZIP. Reader input/scroll behavior, providers, chapter fallback, state, settings
and historical data paths have not been changed by this hardening pass.

## Acceptance checks

| Check | Result |
| --- | --- |
| Complete offline suite, normal-user execution | **418 tests passed** |
| Application offline self-test | **55/55 passed** |
| Python source parsed with the Python 3.11 grammar | Passed |
| `sh -n install.sh uninstall.sh` | Passed |
| Complete release allowlist and SHA256SUMS coverage | Passed |
| Final ZIP extracted and tested again | Same suite/self-test/integrity gates |
| Two release builds from the frozen source | Byte-identical ZIPs |
| Privacy scan and PNG structure/metadata checks | Passed |
| Both public screenshots | Visually inspected; synthetic empty data |
| Staged Git paths and bytes in a disposable local repository | Matched reviewed payload |

The original 320 tests are retained. New tests cover release selection, real
filesystem transactions, setup integration and failure paths. Parameterized cases
and subtests are not counted as repeated full-suite runs.

## Negative and recovery cases

- The builder refuses source/ancestor/HOME destinations and symlink output paths;
  it preserves unrelated existing files. Interrupted ZIP writing does not truncate
  the previous archive.
- The allowlist excludes unreviewed local files. Privacy tests include `.env.local`,
  other environment files, logs, backups, keys, databases and unknown extensions.
  Forced Git additions and staged/working-copy byte differences are checked too.
- Empty, truncated, altered or incomplete manifests fail. Payload symlinks and
  hardlinks fail. Unmanifested source files are never installed.
- Eighteen transaction-crash cases cover create/replace/delete across six commit
  boundaries. Six additional full-installer subcases use actual SIGKILL. Recovery
  is rerun against the surviving filesystem objects, not just a mocked return code.
- Eighteen injected checkpoint exceptions and eighteen injected fsync failure
  boundaries exercise rollback and incomplete-commit handling. A separate failed
  rollback test keeps both copies and successfully recovers after the I/O fault
  is removed.
- SIGINT and SIGTERM, staged/backup failures, readonly destinations, failed mpv
  probes and failed dependency installation preserve the existing installation
  and synthetic reading data in the tested cases.
- Help, package checking, recovery and invalid setup options do not invoke apt.
- Unrelated commands, desktop entries, unsafe links, overlapping/relative XDG paths,
  corrupt backups and modified backup destinations are refused.
- The new launcher's shared lock blocks setup during reading; concurrent setup
  processes are refused. Recovery works without mpv or a network connection.
- Reinstallation, uninstallation, restoration after uninstallation and HOME names
  containing spaces, quotes, percent characters and Unicode are covered.
- Backup helpers are copied from already verified staging, not from source files
  that might have changed after checksum verification.

Current progress and preferences are never rollback targets. The old destructive
`--restore-data` option is rejected. Private backups remain outside the public
payload and newly created backup directories use mode 0700.

## Environment and limits

Executed in **Debian GNU/Linux 13, Python 3.13.5**, as an ordinary non-root user,
with liblua 5.4. Filesystem replacement, advisory locks, subprocess termination and
permission tests are real. mpv capability discovery and apt calls are simulated;
the reader Lua harness uses the real Lua code against a simulated mpv API.

Python 3.11 syntax parsing does **not** mean this container ran Python 3.11.2. An
attempt to obtain a 3.11 test runtime failed because external DNS was unavailable.
This pass does not certify physical power-loss recovery, disk/firmware reliability,
real Xfce/mpv rendering, current external-source availability or a GitHub-hosted
Actions run. A checksum is not a publisher signature. See
[installation safety](docs/INSTALLATION_SAFETY.md) for the exact transaction model,
filesystem requirements and exclusions.

Before public announcement, verify setup on the target desktop, the Git author and
noreply email, and the GitHub-hosted CI result. These checks do not modify the user's
GitHub account or any real reading history.

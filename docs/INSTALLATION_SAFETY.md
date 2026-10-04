# Installation, recovery and publication safety

This document describes the hardened packaging of 0.8.1. The application entry
point, every `acmanga` module, the Lua reader and the English catalogue retain the
accepted 0.8.1 bytes. The changes are confined to setup, release tooling, tests,
CI and documentation.

## Supported environment and permissions

Use a normal Linux user, Python 3.11 or later, and a local filesystem supporting
`renameat2` exchange and directory `fsync`. Debian 12 is the main target. Unsupported
atomic replacement is an error, not a reason to fall back to deleting old code.
The setup tools reject root/sudo execution. Only the optional Debian/Ubuntu system
package bootstrap uses sudo. Required runtime packages are `python3`, `mpv` and
`ca-certificates`; there are no pip dependencies.

Close all readers before installing, restoring or uninstalling. New launchers hold
a shared lock for their whole lifetime; setup tools take the same lock exclusively.
Old launchers are checked through `/proc` as a compatibility precaution. Two setup
processes cannot publish concurrently. An active recovery journal also blocks the
new launcher until recovery completes.

HOME and XDG data paths must be absolute and nonempty, without control characters.
Symlinked managed paths, overlapping data/code locations and unrelated commands or
desktop entries are refused rather than overwritten. A separately named unrelated
`manga` command or link is preserved.

## What happens before the installed code changes

1. With an existing Python, `install.sh` checks the complete release before asking
   to install missing system packages. Without Python, the bootstrap must obtain
   Python first. Failure in apt stops setup before application replacement.
   Help, package checking, recovery and invalid command-line options never invoke
   the system package manager.
2. `release-files.txt` defines the exact approved source payload. The manifest must
   cover it completely: empty, truncated, duplicate, missing or changed checksums
   do not pass. Payload links and special files are refused.
3. Only those verified bytes are copied into an isolated staging directory.
   Unlisted local files are never installed. The staged application must pass its
   offline self-test before proceeding.
4. Existing code, managed launchers, desktop entry and reading data are copied to
   a unique private backup. Copied bytes and modes are compared with the source,
   and recovery helpers travel with the backup. Backup directories use mode 0700.
5. All replacement objects are prepared before publication, with flushed files and
   directories. Any failure here leaves the existing code and reading data alone.

## Publication and recovery

The journal records the expected destinations and original/new filesystem object
identities before changes start. An existing app directory is exchanged with the
prepared directory atomically; it is not recursively deleted to make room.
Launchers and the desktop file follow the same replacement model.

The group of paths is not one indivisible filesystem operation. Until its durable
commit marker is written, an interrupted transaction is recovered to its previous
objects. After the marker, recovery only finishes cleanup. Recovery itself is
repeatable if it was interrupted. Unknown paths or unexpected objects are kept
for inspection, not recursively removed.

Normal Python exceptions, Ctrl-C, SIGTERM and SIGHUP attempt immediate recovery.
After SIGKILL, a reboot or a closed terminal, run from the extracted release:

```sh
bash install.sh --recover
```

This does not require mpv or a network connection. Python is still required. The
next normal install/restore/uninstall also checks for an interrupted transaction.
Do not manually delete a pending journal or its staging directories: they may
contain the preserved previous installation. If an I/O error prevents recovery,
the tool retains these objects and reports the problem rather than calling it a
successful installation.

## Reading data and backups

No installation transaction targets `state.json`, `settings.json`, caches or
reading history. Their historical locations remain unchanged. Setup cannot
silently reset progress as part of a rollback.

The `restore.py` command printed by the installer verifies the chosen backup and
stages its code before changing anything. It refuses altered backup destinations
and damaged snapshot bytes. It preserves current reading data. The former
`--restore-data` option is rejected; overwriting newer progress is not an automatic
setup operation. Data snapshots remain available for a deliberate manual recovery.

Uninstall uses the same locking and recovery machinery, removes only recognized
code/launchers, and preserves reading data, caches and private backups. These
hardened helpers are included in newly created backups. Older backup scripts are
not retroactively rewritten; use the current installer to reinstall an older
trusted payload rather than assuming a legacy restore script has these safeguards.

## Release and Git guardrails

The builder packages only `release-files.txt`, not a recursive guess at which local
files are public. A privacy scan rejects private/runtime names and unreviewed files,
checks common secret/personal-path patterns, and inspects image structure and
metadata. Two supplied screenshots also receive visual review. This is not an
algorithmic guarantee that arbitrary image pixels or unknown secret formats are
nonprivate.

The builder never deletes a user-selected output directory. It refuses source,
ancestor, HOME and symlink destinations, preserves unrelated existing files, writes
a ZIP to a unique temporary file and publishes the completed file. ZIP and sidecar
checksums are separate atomic writes: a crash between them can leave a detectable
mismatch. Rebuilding repairs the pair; do not publish a mismatched pair.

Before a push, stage the reviewed files and run:

```sh
python3 tools/privacy_check.py --git-index
```

This also checks files force-added despite `.gitignore`, symlinks/submodules, missing
payload files and staged bytes that differ from the reviewed working copy. It
checks the current index, not historical commits. Review commit author/email and
Git history separately. Never push old recovery archives or personal state.

## Limits of these guarantees

The tests exercise real filesystem renames, locks, permissions and process death
in temporary HOME directories, plus injected disk-full/I/O failures. The mpv
capability probe and package manager are simulated; the Lua reader uses a simulated
mpv API. SIGKILL tests are not physical power-cut tests. Successful `fsync` calls
cannot repair a failing disk, lying storage firmware, a corrupt filesystem, hostile
same-user processes or later user edits. The application data writer is unchanged;
this is not a new audit of every possible runtime/source failure.

System packages installed by apt are managed by apt, not undone by the application
rollback. Keep independent backups of important reading data. SHA-256 detects
accidental changes relative to a known checksum; it does not authenticate a publisher.
GitHub-hosted CI and a final setup check on the target desktop remain separate
release gates.

## Filesystem API references

- Linux `rename(2)`, including `RENAME_EXCHANGE` and `RENAME_NOREPLACE`:
  https://man7.org/linux/man-pages/man2/rename.2.html
- Python `os.fsync` / `os.replace`:
  https://docs.python.org/3.11/library/os.html

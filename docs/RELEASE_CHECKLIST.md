# Release and privacy checklist

1. Run `python3 tools/privacy_check.py` and inspect every finding rather than
   weakening the check to make it pass.
2. Run `python3 tools/sync_text.py --check`, the complete unit suite and
   `python3 manga.py --self-test`.
3. Check shell syntax with `sh -n install.sh uninstall.sh` and validate dependency
   bootstrap behavior without running the application installer as root.
4. Complete `docs/MANUAL_VALIDATION.md` on Debian 12/Xfce/mpv and run live-source
   checks only when network requests are intended.
5. Confirm version consistency in `VERSION`, `acmanga/__init__.py`, reader help,
   installer output, screenshots and release documentation.
6. Build with `python3 tools/build_release.py`; do not hand-edit `SHA256SUMS`.
   Extract the produced ZIP and verify it again from the extracted copy.
7. Confirm the release contains no state/settings/history/progress, caches, logs,
   backups, bytecode, environment files, keys, tokens, personal home paths or
   recovery archives. Inspect image metadata as well as text files.
8. Run `python3 tools/privacy_check.py --git-index` after staging. Review `git diff --cached`, `git status --short` and `git ls-files`. Remember
   that `.gitignore` cannot erase data already committed to Git history.
9. Review the commit author name/email that GitHub will expose. Use the intended
   public identity and a private/noreply email configuration if desired.
10. Confirm `LICENSE`, `THIRD_PARTY.txt`, `SECURITY.md`, CI and issue templates are
    present. Verify attribution and that no repository URLs contain private account
    information before the first push.

The ZIP checksum detects accidental changes relative to a built release. It is not
a cryptographic signature establishing publisher identity.

11. Review new allowlist entries and the complete failure-injection suite. Do not
    package by recursively archiving a working HOME directory. Confirm installation
    and recovery work as a normal user on the target desktop before announcing the
    release, and wait for the GitHub-hosted CI run to pass.

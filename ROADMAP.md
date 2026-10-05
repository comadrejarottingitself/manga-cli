# Roadmap

## Current release: 0.8.3

Public-release baseline: English UI/documentation, ten accent colors, safe
single-line information frames, automatic/manual source selection, MangaKatana /
MangaPill fallback, reproducible release packaging, CI and privacy guardrails.

The 0.8.3 patch keeps the established reader navigation and adds three narrowly
scoped improvements discovered through real use: runtime compatibility with both
legacy and modern mpv background options, bidirectional page prefetch with a new
10-page-per-side choice, and uppercase `F`/`V` fit toggles for Caps Lock users.

The 0.8.2 delayed `vo=gpu` -> `vo=x11` startup fallback and Bash PATH fix remain
unchanged. The publication tooling also keeps dependency setup straightforward on Debian:
`install.sh` can install missing `python3`/`mpv` apt packages before performing the
per-user transactional application install.

## Release discipline

Before every public release, complete the Debian 12/Xfce/mpv manual checks and live
source tests, run the automated suite, rebuild the integrity manifest and inspect
the staged Git tree/commit metadata for private information. Source adapters are
external-site integrations and may need maintenance independently of the reader.

## Later, separately scoped releases

- Expanded information view for full alternative titles and descriptions.
- Additional application languages using the centralized catalogue.
- Data-directory renaming only with an explicit compatibility/migration plan,
  recoverable backups and round-trip tests.
- Additional source adapters after identity/progress safety tests.

Reader navigation changes are not planned merely for visual or repository polish.

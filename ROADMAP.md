# Roadmap

## Current release: 0.8.2

Public-release baseline: English UI/documentation, ten accent colors, safe
single-line information frames, automatic/manual source selection, MangaKatana /
MangaPill fallback, reproducible release packaging, CI and privacy guardrails.

The 0.8.2 patch keeps the 0.8.1 interface and reader controls, while fixing two
issues found in a clean Debian 12/Xfce VM: delayed `vo=gpu` failure now falls back
to `vo=x11`, and new Bash terminals receive the per-user `~/.local/bin` path.

The publication tooling also keeps dependency setup straightforward on Debian:
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

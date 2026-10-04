# Security policy

## Supported version

Security fixes are applied to the current public release of MANGA-CLI.

## Reporting a vulnerability

Please use GitHub's **Security** tab and open a private security advisory instead
of filing a public issue. Do not include passwords, tokens, private URLs, reading
history, home-directory paths or other personal data in a public report.

For ordinary source-site breakage, parser failures or UI bugs, use the issue
templates instead. Those are normally reliability issues rather than security
vulnerabilities.

## Scope

MANGA-CLI is a local terminal application. It stores reading state and settings in
the user's data directory and caches pages locally. It does not require accounts,
API keys or browser cookies for its supported sources. The project does not bundle
MangaKatana, MangaPill, mpv or Python.

## Setup and release protections

See `docs/INSTALLATION_SAFETY.md` for explicit payload selection, staged-Git checks,
verified backups, atomic replacement, recovery and the boundaries of these
protections. A passed automated scan is not a claim that arbitrary secrets, image
pixels or past Git history have been exhaustively checked. Keep independent data
backups and do not publish unsanitized local setup/reader logs.

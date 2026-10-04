# Architecture

`manga.py` coordinates terminal menus and reading flows. `acmanga/ui.py` renders
character frames and applies live color roles from `theme.py`. `terminal_text.py`
sanitizes provider text and measures terminal cells before truncating/padding it.

`acmanga/i18n.py` reads the English message catalogue in `locales/en.json`.
`tools/sync_text.py` embeds the relevant catalogue subsets in the standalone Lua
reader and bootstrap/restore message module. These generated tables do not
require a new runtime dependency.

`engine.py` merges source results and preserves canonical identity across source
fallback. `sources/mangakatana.py` and `sources/mangapill.py` parse those providers.
`util.py` and `htmlutil.py` provide HTTP, atomic writes and parser helpers.

`reader.py` owns one mpv process per reading session. `reader.lua` handles the
existing view/navigation/input rules. `streaming.py` provides bounded page
prefetch and cache management. Only presentation messages change in those reader
paths for 0.8; protocol keys and mpv arguments remain unchanged.

`state.py` retains schema 6 for saved manga, progress and availability. It is
unchanged from the supplied 0.7.5 file. `settings.py` retains schema 2 and adds the
validated `accent_color` field; legacy language fields are ignored.

The setup tools share `tools/_transaction.py`: fsynced staging, private verified
backups, atomic exchange, durable journaling and shared/exclusive process locks.
`tools/launch.py` keeps a shared lock across the unchanged application's lifetime.
`tools/_release.py` supplies the explicit payload allowlist and complete checksum
verification. No transaction writes reading data, migrates paths or edits global
mpv/ani-cli configuration. Backup recovery helpers are self-contained. See
`INSTALLATION_SAFETY.md` for failure semantics and supported filesystems.

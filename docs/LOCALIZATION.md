# Localization and terminal text

## Single source of English messages

Edit `acmanga/locales/en.json`. Python application modules access it through
`tr(key)` or `tr(key, *args, **kwargs)`. Keys are descriptive and scoped by module.
English is the only application locale in this release; there is deliberately no
nonfunctional language selector.

The Lua reader cannot depend on the Python process or extra Lua modules to show
help/errors. `tools/sync_text.py` embeds its `lua.*` subset in a marked generated
block. The same tool generates `tools/_messages.py` for independent installer and
rollback execution. Do not edit generated tables by hand.

```sh
python3 tools/sync_text.py
python3 tools/sync_text.py --check
```

State keys, source identifiers, machine diagnostics, parser patterns and Lua/IPC
commands are not translatable. Titles and source-supplied metadata stay in their
original form. For a future locale, preserve format placeholders and add tests
before exposing a language preference.

## Width and untrusted provider text

The frame renderer reserves label space, budgets the value column, truncates with
ASCII `...`, then pads by cells, not Python string length. East Asian W/F
characters count as two; combining marks count as zero. Ambiguous-width symbols
use a one-cell Western UTF-8 terminal convention. Tabs/newlines become spaces;
terminal escape sequences and invisible format controls are removed.

This covers Latin, decomposed accents and CJK text used by the information card.
It is not a complete terminal-independent grapheme/emoji renderer: font, emoji
presentation, terminal settings and Unicode-version differences can affect
unusual symbols. The strategy is conservative and may truncate such text early.
Test on the intended terminal before claiming universal glyph-width accuracy.

References: Python 3.11's unicodedata documentation and Unicode UAX #11 (links in
THIRD_PARTY.txt). No dependency or font has been added to the runtime.

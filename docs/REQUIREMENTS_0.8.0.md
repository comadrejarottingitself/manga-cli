# 0.8.0 requirements and verification map

| Requirement | Verification |
| --- | --- |
| Build directly on 0.7.5 | Baseline suite plus normalized reader/parser contract hashes |
| English UI, help, messages and docs | Catalogue coverage, screen snapshots, generated-text check, CLI smoke tests |
| Ten accents, Crimson default | Palette set/default/uniqueness tests |
| Live preview, save, cancel, persistence | Appearance-flow tests including failed writes and reopen |
| Stable semantic colors | Success/warning/error role tests across all ten accents |
| CJK-safe single-line `...` | Cell-width, combining-mark, oversized-column and multi-width frame tests |
| Remove language UI, accept old settings | Screen assertions and legacy configuration normalization tests |
| Preserve reader behavior | Real Lua harness, Python/Lua bridge, existing controller/sequence/prefetch tests |
| Preserve private data and old paths | Installer/rollback/XDG tests and unchanged state.py hash |
| No global mpv/ani-cli changes | Installer sentinel-file tests and argument/source checks |
| Repository-ready layout | Documentation, LICENSE placeholder, clean-archive scan and release manifest |
| Target desktop acceptance | Manual checklist; not certified by the build environment |

0.8.0 does not migrate historical directories or
claim live-source/visual mpv validation that has not been performed.

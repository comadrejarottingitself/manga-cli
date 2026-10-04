# 0.8.1 requirements and verification map

0.8.1 is a presentation-only patch on 0.8.0.

| Requirement | Verification |
| --- | --- |
| Home subtitle omits provider abbreviations | UI regression test |
| Main screen does not advertise `C Continue` | UI regression test; shortcut remains accepted |
| About links the tutorial/guide to `README.md` | UI regression test |
| About contains no license-status row | UI regression test |
| Visible signature is `a comadreja project` | UI and generated-reader text checks |
| Reader/source behavior unchanged | full inherited suite + reader.py hash comparison |
| Public open-source license | MIT `LICENSE` present |

No data-directory migration is performed. Existing state and settings remain protected by the transactional installer.

## Summary

Describe the change and why it is needed.

## Validation

- [ ] `python3 tools/privacy_check.py`
- [ ] `python3 tools/sync_text.py --check`
- [ ] `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v`
- [ ] `python3 manga.py --self-test`
- [ ] Reader behavior is unchanged, or behavior changes include focused regression tests.
- [ ] No personal state, history, logs, caches, credentials or user-specific paths are included.

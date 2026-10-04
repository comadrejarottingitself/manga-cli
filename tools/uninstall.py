#!/usr/bin/env python3
"""Remove only managed code/launchers, with the same recoverable transaction."""
from __future__ import annotations

import argparse
import sys

from _messages import tr
import _transaction as tx


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    tx.require_user()
    paths = tx.Paths()
    with tx.handle_signals(), tx.install_lock(paths):
        tx.check_running(paths)
        tx.recover(paths)
        tx.check_targets(paths)
        keys = []
        if paths.app.exists():
            keys.append('app')
        for key in ('launcher', 'legacy'):
            if tx.owned(paths.targets[key]):
                keys.append(key)
        if paths.desktop.exists():
            keys.append('desktop')
        entries = []
        try:
            for key in keys:
                entries.append(tx.prepare(paths, key))
            tx.transact(paths, entries)
        finally:
            if not paths.journal.exists():
                tx.cleanup(entries, paths)
        tx.remove_bash_path(paths)
        print(tr('setup.manga_cli_uninstalled_saved_manga_settings_cache_and_backups_were_preser'))


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print('ERROR: ' + (str(exc) or 'Interrupted.'), file=sys.stderr)
        sys.exit(1)

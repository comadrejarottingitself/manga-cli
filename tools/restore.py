#!/usr/bin/env python3
"""Restore a verified private code backup, never historical reading data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from _messages import tr
import _transaction as tx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()  # Unknown/destructive options are rejected before any writes.
    tx.require_user()
    paths = tx.Paths()
    root = Path(__file__).resolve().parent
    if root.parent != paths.backups:
        raise RuntimeError('Run restore.py inside its original private backup directory.')
    tx.no_links(root)
    with tx.handle_signals(), tx.install_lock(paths):
        tx.check_running(paths)
        tx.recover(paths)
        tx.check_targets(paths)
        tx.no_links(root/'manifest.json')
        record = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        if (record.get('schema') != 2 or record.get('home') != str(paths.home)
                or record.get('data_home') != str(paths.data_home)):
            raise RuntimeError('Backup belongs to a different HOME/XDG_DATA_HOME or is not a supported verified backup.')
        expected_paths = dict(paths.targets, data=paths.data)
        if set(record.get('paths', {})) != set(expected_paths):
            raise RuntimeError('Backup manifest is incomplete.')
        for key, expected in expected_paths.items():
            item = record['paths'][key]
            if item.get('path') != str(expected) or type(item.get('existed')) is not bool:
                raise RuntimeError('Unsafe backup destination; nothing has been replaced.')
            if item['existed'] and tx.inventory(root/key) != record['inventories'].get(key):
                raise RuntimeError('Backup integrity check failed; current installation was not replaced.')
        for name in ('restore.py', '_transaction.py', '_messages.py'):
            if tx.inventory(root/name) != record.get('helpers', {}).get(name):
                raise RuntimeError('Backup recovery helper integrity check failed.')
        keys = ['app', 'launcher', 'desktop']
        if record.get('legacy_managed') and (tx.owned(paths.legacy) or not (paths.legacy.exists() or paths.legacy.is_symlink())):
            keys.append('legacy')
        entries = []
        try:
            for key in keys:
                source = root/key if record['paths'][key]['existed'] else None
                entries.append(tx.prepare(paths, key, source=source))
            tx.transact(paths, entries)
        finally:
            if not paths.journal.exists():
                tx.cleanup(entries, paths)
        print(tr('setup.previous_code_restored_current_saved_manga_and_progress_were_preserved'))


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print('ERROR: ' + (str(exc) or 'Interrupted.'), file=sys.stderr)
        sys.exit(1)

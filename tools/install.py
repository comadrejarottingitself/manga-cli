#!/usr/bin/env python3
"""Install only verified release bytes, with atomic replacement and recovery."""
from __future__ import annotations

import argparse
import datetime
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from _messages import tr
from _release import verified_payload
import _transaction as tx

ROOT = Path(__file__).resolve().parents[1]
APP = tx.APP
MARKER = tx.MARKER
owned = tx.owned
INSTALL_TOP = {'manga.py', 'VERSION', 'acmanga', 'README.md', 'README.txt', 'CHANGELOG.md',
               'VALIDATION.md', 'DEBIAN12.txt', 'LICENSE', 'CONTRIBUTING.md', 'ROADMAP.md',
               'SECURITY.md', 'THIRD_PARTY.txt', 'docs', 'uninstall.sh', 'tools'}


def check_package(root):
    return verified_payload(root)


def private_backup(paths, legacy_managed, verified_stage):
    paths.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    tx.no_links(paths.backups)
    if paths.backups.stat().st_uid != os.geteuid():
        raise RuntimeError('Backup directory is owned by another user.')
    backup = Path(tempfile.mkdtemp(prefix=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-'), dir=paths.backups))
    backup.chmod(0o700)
    record = dict(schema=2, home=str(paths.home), data_home=str(paths.data_home),
                  paths={}, inventories={}, helpers={}, legacy_managed=legacy_managed)
    for key, path in dict(paths.targets, data=paths.data).items():
        existed = path.exists() or path.is_symlink()
        record['paths'][key] = {'path': str(path), 'existed': existed}
        if existed:
            tx.copy_object(path, backup/key)
            record['inventories'][key] = tx.inventory(backup/key)
    # A backup remains self-contained even after code replacement or uninstall.
    for name in ('restore.py', '_transaction.py', '_messages.py'):
        tx.copy_object(verified_stage/'tools'/name, backup/name)
        record['helpers'][name] = tx.inventory(backup/name)
    tx.atomic_json(backup/'manifest.json', record)
    tx.sync_tree(backup)
    tx.sync_dir(paths.backups)
    tx.sync_dir(paths.backups.parent)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='verify the release before installing dependencies')
    parser.add_argument('--recover', action='store_true', help='recover an interrupted code transaction without installing anything')
    args = parser.parse_args()
    tx.require_user()
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or later is required for this tested Linux release.')
    if args.check:
        check_package(ROOT)
        print('Package integrity verified.')
        return
    paths = tx.Paths()
    with tx.handle_signals(), tx.install_lock(paths):
        tx.check_running(paths)
        recovered = tx.recover(paths)
        if args.recover:
            print('Interrupted transaction recovered. Reading data were not changed.' if recovered else 'No interrupted transaction found.')
            return
        tx.check_targets(paths)
        # Nothing installed or backed up until the entire approved payload is checked.
        payload = check_package(ROOT)
        mpv = shutil.which('mpv')
        if not mpv:
            raise RuntimeError(tr('setup.mpv_is_missing_on_debian_sudo_apt_install_mpv'))
        vo = subprocess.run([mpv, '--no-config', '--vo=help'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=5)
        outputs = {line.strip().split()[0] for line in vo.stdout.splitlines() if line.strip()}
        if vo.returncode or not outputs.intersection({'gpu', 'x11'}):
            raise RuntimeError(tr('setup.mpv_with_gpu_or_x11_video_output_is_required'))
        legacy_managed = tx.owned(paths.legacy) or not (paths.legacy.exists() or paths.legacy.is_symlink())
        stage = Path(tempfile.mkdtemp(prefix='.manga-cli-verified-', dir=paths.app.parent))
        entries = []
        backup = None
        try:
            for rel, data in payload.items():
                if Path(rel).parts[0] not in INSTALL_TOP:
                    continue
                target = stage/rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o755 if rel == 'uninstall.sh' else 0o644)
            env = os.environ.copy()
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            test = subprocess.run([sys.executable, str(stage/'manga.py'), '--self-test'], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
            if test.returncode:
                raise RuntimeError(tr('setup.the_staged_copy_failed_its_self_test') + test.stdout)
            tx.checkpoint('stage_verified')
            backup = private_backup(paths, legacy_managed, stage)
            tx.checkpoint('backup_verified')
            entries.append(tx.prepare(paths, 'app', source=stage))
            command = '#!/bin/sh\n' + MARKER + '\nexec python3 ' + shlex.quote(str(paths.app/'tools/launch.py')) + ' "$@"\n'
            entries.append(tx.prepare(paths, 'launcher', content=command.encode('utf-8')))
            if legacy_managed:
                alias = '#!/bin/sh\n' + MARKER + '\nexec ' + shlex.quote(str(paths.launcher)) + ' "$@"\n'
                entries.append(tx.prepare(paths, 'legacy', content=alias.encode('utf-8')))
            desktop = ('[Desktop Entry]\nType=Application\nName=manga-cli\nComment=MANGA-CLI - terminal manga reader\n'
                       + 'Exec="' + tx.desktop_escape(paths.launcher) + '"\nIcon=accessories-dictionary\nTerminal=true\nCategories=Office;Viewer;\n')
            entries.append(tx.prepare(paths, 'desktop', content=desktop.encode('utf-8'), mode=0o644))
            tx.check_targets(paths)
            tx.check_running(paths)
            shutil.rmtree(stage)
            tx.transact(paths, entries)
        finally:
            # If recovery is pending, its copies must survive. The independent initial
            # verification stage is disposable; it has never been an installed object.
            if not paths.journal.exists():
                tx.cleanup(entries, paths)
            if stage.exists():
                shutil.rmtree(stage)
        print(tr('setup.manga_cli_0_8_1_installed_saved_manga_and_settings_were_not_modified'))
        print(tr('setup.command') + str(paths.launcher))
        if legacy_managed:
            print(tr('setup.the_manga_command_remains_as_a_compatibility_alias'))
        print(tr('setup.private_backup') + str(backup))
        print(tr('setup.to_restore_the_previous_code_while_keeping_your_current_progress'))
        print('  python3 ' + shlex.quote(str(backup/'restore.py')))
        if str(paths.launcher.parent) not in os.environ.get('PATH', '').split(os.pathsep):
            print(tr('setup.open_a_new_terminal_or_run') + str(paths.launcher))


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print('ERROR: ' + (str(exc) or 'Interrupted.'), file=sys.stderr)
        sys.exit(1)

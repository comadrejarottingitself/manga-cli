"""Real-filesystem transactions, fault injection and abrupt-process recovery."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT/'tools') not in sys.path:
    sys.path.insert(0, str(ROOT/'tools'))
import _transaction as tx

PHASES = ('journal', 'app', 'launcher', 'legacy', 'desktop', 'committed')


class TransactionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.home = self.base/'home'; self.home.mkdir()
        env = os.environ.copy(); env['HOME'] = str(self.home)
        env.pop('XDG_DATA_HOME', None); env.pop('SUDO_USER', None)
        self.env = env
        patch = mock.patch.dict(os.environ, env, clear=True); patch.start(); self.addCleanup(patch.stop)
        self.paths = tx.Paths()
        self.paths.data.mkdir(parents=True)
        (self.paths.data/'state.json').write_bytes(b'SYNTHETIC CURRENT PROGRESS\n')
        self.saved = tx.inventory(self.paths.data)

    def prepare(self, kind):
        entries = []
        self.before = {}; self.after = {}
        for key, target in self.paths.targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind != 'create':
                if key == 'app':
                    target.mkdir(); (target/'manga.py').write_text('old code')
                else:
                    target.write_text('old ' + key)
            self.before[key] = tx.inventory(target) if target.exists() else None
            source = None
            if kind != 'delete':
                source = self.base/('new-' + key)
                if key == 'app':
                    source.mkdir(); (source/'manga.py').write_text('new code')
                else:
                    source.write_text('new ' + key)
            self.after[key] = tx.inventory(source) if source is not None else None
            entries.append(tx.prepare(self.paths, key, source=source))
        return entries

    def assert_contents(self, expected):
        for key, target in self.paths.targets.items():
            self.assertEqual(tx.inventory(target) if target.exists() else None, expected[key], key)
        self.assertEqual(tx.inventory(self.paths.data), self.saved)
        self.assertFalse(self.paths.journal.exists())
        self.assertFalse(list(self.paths.app.parent.glob('.manga-cli-txn-*')))
        self.assertFalse(list(self.paths.launcher.parent.glob('.manga-cli-txn-*')))
        self.assertFalse(list(self.paths.desktop.parent.glob('.manga-cli-txn-*')))

    def exception_case(self, kind, phase):
        entries = self.prepare(kind)
        def fail(name):
            if name == phase:
                raise OSError(errno.ENOSPC, 'simulated disk-full checkpoint')
        with mock.patch.object(tx, 'checkpoint', side_effect=fail):
            with self.assertRaises(OSError):
                tx.transact(self.paths, entries)
        self.assert_contents(self.after if phase == 'committed' else self.before)

    def crash_case(self, kind, phase):
        entries = self.prepare(kind)
        script = '''
import os, sys
sys.path.insert(0, sys.argv[1])
import _transaction as tx
import json
phase = sys.argv[2]
def crash(name):
    if name == phase:
        os._exit(91)
tx.checkpoint = crash
paths = tx.Paths()
with tx.install_lock(paths):
    tx.transact(paths, json.loads(sys.argv[3]))
'''
        run = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT/'tools'), phase, json.dumps(entries)],
                             env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 91, run.stdout+run.stderr)
        self.assertTrue(self.paths.journal.is_file())
        # The application directory is whole even before the recovery command runs.
        if kind == 'replace':
            self.assertTrue((self.paths.app/'manga.py').is_file())
        with tx.install_lock(self.paths):
            self.assertTrue(tx.recover(self.paths))
            self.assertFalse(tx.recover(self.paths))
        self.assert_contents(self.after if phase == 'committed' else self.before)

    def test_atomic_replacement_unavailable_keeps_old_installation(self):
        entries = self.prepare('replace')
        with mock.patch.object(tx, 'rename_atomic', side_effect=OSError(errno.ENOSYS, 'unsupported')):
            with self.assertRaises(OSError):
                tx.transact(self.paths, entries)
        self.assert_contents(self.before)

    def test_journal_write_failure_before_mutation_keeps_old_installation(self):
        entries = self.prepare('replace')
        with mock.patch.object(tx, 'atomic_json', side_effect=OSError(errno.EACCES, 'denied')):
            with self.assertRaises(OSError):
                tx.transact(self.paths, entries)
        tx.cleanup(entries, self.paths)
        self.assert_contents(self.before)

    def test_failed_rollback_keeps_journal_and_both_copies_for_retry(self):
        entries = self.prepare('replace')
        rename = tx.rename_atomic
        calls = []
        def fail_after_first(*args, **kwargs):
            calls.append(args)
            if len(calls) > 1:
                raise OSError(errno.EIO, 'simulated unavailable disk')
            return rename(*args, **kwargs)
        with mock.patch.object(tx, 'rename_atomic', side_effect=fail_after_first):
            with self.assertRaises(RuntimeError):
                tx.transact(self.paths, entries)
        self.assertTrue(self.paths.journal.exists())
        self.assertTrue(self.paths.app.is_dir())
        self.assertTrue((Path(entries[0]['holder'])/'new').is_dir())
        self.assertTrue(tx.recover(self.paths))
        self.assert_contents(self.before)

    def test_full_disk_at_multiple_fsync_boundaries_recovers(self):
        # Each subcase has its own filesystem tree. These are different write stages,
        # not repetitions of the same successful install.
        for fail_at in range(1, 19):
            with self.subTest(fsync_call=fail_at):
                case = TransactionSafetyTests('runTest'); case.setUp()
                try:
                    entries = case.prepare('replace')
                    real = tx.os.fsync; calls = [0]
                    def one_failure(fd):
                        calls[0] += 1
                        if calls[0] == fail_at:
                            raise OSError(errno.ENOSPC, 'simulated full disk')
                        return real(fd)
                    with mock.patch.object(tx.os, 'fsync', side_effect=one_failure):
                        try:
                            tx.transact(case.paths, entries)
                        except (OSError, RuntimeError):
                            pass
                    if case.paths.journal.exists():
                        tx.recover(case.paths)
                    tx.cleanup(entries, case.paths)
                    current = {key: tx.inventory(p) if p.exists() else None for key, p in case.paths.targets.items()}
                    self.assertIn(current, [case.before, case.after])
                    self.assertEqual(tx.inventory(case.paths.data), case.saved)
                finally:
                    case.doCleanups()

    def test_unexpected_recovery_path_is_refused_without_deletion(self):
        entries = self.prepare('replace')
        sentinel = self.base/'unrelated'; sentinel.write_text('KEEP')
        entries[0]['target'] = str(sentinel)
        tx.atomic_json(self.paths.journal, dict(schema=1, home=str(self.home), data_home=str(self.paths.data_home), state='pending', entries=entries))
        with self.assertRaises(RuntimeError):
            tx.recover(self.paths)
        self.assertEqual(sentinel.read_text(), 'KEEP')
        self.assertTrue((self.paths.app/'manga.py').is_file())

    def test_unexpected_file_in_staging_prevents_recursive_deletion(self):
        entries = self.prepare('replace')
        sentinel = Path(entries[0]['holder'])/'unrelated'; sentinel.write_text('KEEP')
        with self.assertRaises(RuntimeError):
            tx.transact(self.paths, entries)
        self.assertEqual(sentinel.read_text(), 'KEEP')

    def test_second_installer_cannot_obtain_lock(self):
        script = 'import sys;sys.path.insert(0,sys.argv[1]);import _transaction as t;\nwith t.install_lock(t.Paths()): print("UNEXPECTED")'
        with tx.install_lock(self.paths):
            result = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT/'tools')], env=self.env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Another install', result.stderr)
        with tx.install_lock(self.paths):
            pass

    def test_root_execution_is_refused(self):
        with mock.patch.object(tx.os, 'geteuid', return_value=0), self.assertRaises(RuntimeError):
            tx.require_user()

    def test_relative_empty_and_control_character_xdg_values_are_refused(self):
        for value in ['relative', '', str(self.home)+'/bad\npath']:
            with self.subTest(value=value), mock.patch.dict(os.environ, XDG_DATA_HOME=value), self.assertRaises(RuntimeError):
                tx.Paths()

    def test_code_data_overlap_is_refused(self):
        with mock.patch.dict(os.environ, XDG_DATA_HOME=str(self.home/'.local/lib')), self.assertRaises(RuntimeError):
            tx.Paths()

    def test_symlinked_user_bin_directory_is_refused(self):
        self.paths.launcher.parent.parent.mkdir(parents=True, exist_ok=True)
        outside = self.base/'outside'; outside.mkdir()
        self.paths.launcher.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            tx.Paths()
        self.assertEqual(list(outside.iterdir()), [])


# Separate test names make every operation and crash boundary visible in CI.
for _kind in ('replace', 'create', 'delete'):
    for _phase in PHASES:
        def _exception(self, kind=_kind, phase=_phase):
            self.exception_case(kind, phase)
        def _crash(self, kind=_kind, phase=_phase):
            self.crash_case(kind, phase)
        setattr(TransactionSafetyTests, 'test_' + _kind + '_exception_after_' + _phase, _exception)
        setattr(TransactionSafetyTests, 'test_' + _kind + '_crash_after_' + _phase, _crash)

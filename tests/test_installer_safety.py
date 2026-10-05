"""End-to-end setup checks in an isolated user HOME; mpv capability probe is fake."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

from tests import test_installer as fixtures

ROOT = Path(__file__).resolve().parents[1]


class InstallerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.InstallerTests('runTest'); self.f.setUp(); self.addCleanup(self.f.tearDown)

    def run_tool(self, script, *args):
        return subprocess.run([sys.executable, '-B', str(script), *args], env=self.f.env,
                              capture_output=True, text=True, timeout=30)

    def clone(self):
        clone = self.f.base/'package'
        shutil.copytree(ROOT, clone, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'dist', '.git'))
        return clone

    def test_unmanifested_code_is_not_installed(self):
        clone = self.clone()
        (clone/'acmanga/local_secret.py').write_text('DUMMY = True')
        result = self.f.run_install(clone)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertFalse((self.f.home/'.local/lib/anticomadreja-manga/acmanga/local_secret.py').exists())

    def test_empty_manifest_is_not_accepted(self):
        app, data = self.f.seed()
        clone = self.clone(); (clone/'SHA256SUMS').write_text('')
        result = self.f.run_install(clone)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app/'VERSION').read_text(), '0.6.3')

    def test_missing_manifest_entry_is_not_accepted(self):
        app, _ = self.f.seed(); clone = self.clone()
        manifest = clone/'SHA256SUMS'
        manifest.write_text('\n'.join(manifest.read_text().splitlines()[1:])+'\n')
        result = self.f.run_install(clone)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app/'VERSION').read_text(), '0.6.3')

    def test_corrupt_backup_is_rejected_before_replacing_working_code(self):
        app, data = self.f.seed()
        result = self.f.run_install(); self.assertEqual(result.returncode, 0, result.stderr)
        (data/'state.json').write_text('NEW CURRENT PROGRESS')
        backup = self.f.backups()[-1]
        (backup/'app/manga.py').write_text('CORRUPTED')
        before = (app/'manga.py').read_bytes()
        result = self.run_tool(backup/'restore.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('integrity', result.stderr.lower())
        self.assertEqual((app/'manga.py').read_bytes(), before)
        self.assertEqual((data/'state.json').read_text(), 'NEW CURRENT PROGRESS')

    def test_modified_backup_destination_is_rejected(self):
        app, _ = self.f.seed()
        result = self.f.run_install(); self.assertEqual(result.returncode, 0, result.stderr)
        backup = self.f.backups()[-1]
        sentinel = self.f.base/'unrelated'; sentinel.write_text('KEEP')
        manifest = backup/'manifest.json'; record = json.loads(manifest.read_text())
        record['paths']['app']['path'] = str(sentinel)
        manifest.write_text(json.dumps(record))
        result = self.run_tool(backup/'restore.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), 'KEEP')
        self.assertEqual((app/'VERSION').read_text().strip(), '0.8.3')

    def test_destructive_restore_data_flag_is_rejected(self):
        _, data = self.f.seed()
        result = self.f.run_install(); self.assertEqual(result.returncode, 0, result.stderr)
        (data/'state.json').write_text('CURRENT')
        result = self.run_tool(self.f.backups()[-1]/'restore.py', '--restore-data')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((data/'state.json').read_text(), 'CURRENT')

    def test_install_paths_with_spaces_quotes_percent_and_unicode(self):
        home = self.f.base/'home with space quote\' percent% caf\u00e9'; home.mkdir()
        self.f.home = home; self.f.env['HOME'] = str(home)
        for _ in range(2):
            result = self.f.run_install(); self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        result = subprocess.run([str(home/'.local/bin/manga-cli'), '--version'], env=self.f.env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '0.8.3')
        undo = self.run_tool(self.f.backups()[-1]/'restore.py')
        self.assertEqual(undo.returncode, 0, undo.stderr)

    def test_linked_primary_command_is_refused(self):
        sentinel = self.f.base/'outside'; sentinel.write_text('KEEP')
        command = self.f.home/'.local/bin/manga-cli'; command.parent.mkdir(parents=True)
        command.symlink_to(sentinel)
        result = self.f.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), 'KEEP')
        self.assertTrue(command.is_symlink())

    def test_unrelated_legacy_symlink_is_not_modified(self):
        sentinel = self.f.base/'outside'; sentinel.write_text('KEEP')
        command = self.f.home/'.local/bin/manga'; command.parent.mkdir(parents=True)
        command.symlink_to(sentinel)
        result = self.f.run_install(); self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sentinel.read_text(), 'KEEP')
        self.assertTrue(command.is_symlink())

    def test_foreign_desktop_entry_is_not_overwritten(self):
        desktop = self.f.home/'.local/share/applications/manga-cli.desktop'
        desktop.parent.mkdir(parents=True); desktop.write_text('UNRELATED')
        result = self.f.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(desktop.read_text(), 'UNRELATED')
        self.assertFalse((self.f.home/'.local/lib/anticomadreja-manga').exists())

    def test_backup_parent_symlink_is_refused(self):
        link = self.f.home/'.local/share/anticomadreja-manga-backups'
        link.parent.mkdir(parents=True)
        outside = self.f.base/'outside'; outside.mkdir()
        link.symlink_to(outside, target_is_directory=True)
        result = self.f.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_unrecognized_existing_app_directory_is_not_deleted(self):
        app = self.f.home/'.local/lib/anticomadreja-manga'; app.mkdir(parents=True)
        (app/'unrelated.txt').write_text('KEEP')
        result = self.f.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app/'unrelated.txt').read_text(), 'KEEP')

    def test_uninstall_refuses_unrecognized_directory_even_with_version_file(self):
        app = self.f.home/'.local/lib/anticomadreja-manga'; app.mkdir(parents=True)
        (app/'VERSION').write_text('0.8.1')
        (app/'unrelated.txt').write_text('KEEP')
        result = self.run_tool(ROOT/'tools/uninstall.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app/'unrelated.txt').read_text(), 'KEEP')

    def test_uninstall_twice_and_restore_after_uninstall_keep_data(self):
        app, data = self.f.seed()
        result = self.f.run_install(); self.assertEqual(result.returncode, 0, result.stderr)
        backup = self.f.backups()[-1]
        (data/'state.json').write_text('CURRENT')
        for _ in range(2):
            result = self.run_tool(ROOT/'tools/uninstall.py')
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(app.exists())
        result = self.run_tool(backup/'restore.py')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((app/'VERSION').read_text(), '0.6.3')
        self.assertEqual((data/'state.json').read_text(), 'CURRENT')

    def test_running_new_launcher_blocks_installation_for_reader_lifetime(self):
        result = self.f.run_install(); self.assertEqual(result.returncode, 0, result.stderr)
        app = self.f.home/'.local/lib/anticomadreja-manga'
        ready = self.f.base/'ready'
        (app/'manga.py').write_text('from pathlib import Path\nimport time\nPath('+repr(str(ready))+').write_text("ready")\ntime.sleep(15)\n')
        proc = subprocess.Popen([str(self.f.home/'.local/bin/manga-cli')], env=self.f.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if ready.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(ready.exists())
            result = self.f.run_install()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Another install', result.stderr)
        finally:
            proc.terminate(); proc.wait(timeout=5)
        result = self.f.run_install(); self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_command_does_not_require_mpv_or_network(self):
        (self.f.fakebin/'mpv').unlink()
        result = self.run_tool(ROOT/'tools/install.py', '--recover')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No interrupted transaction', result.stdout)

    def test_recovery_refuses_damaged_journal_without_changes(self):
        app, data = self.f.seed()
        journal = app.parent/'.manga-cli-transaction.json'; journal.write_text('INVALID JSON')
        result = self.run_tool(ROOT/'tools/install.py', '--recover')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app/'VERSION').read_text(), '0.6.3')
        self.assertEqual(journal.read_text(), 'INVALID JSON')

    def test_complete_install_survives_sigkill_at_each_publication_boundary(self):
        for phase in ('journal', 'app', 'launcher', 'legacy', 'desktop', 'committed'):
            with self.subTest(phase=phase):
                f = fixtures.InstallerTests('runTest'); f.setUp()
                try:
                    app, data = f.seed()
                    before = {p.name:p.read_bytes() for p in data.iterdir()}
                    script = '''
import os, signal, sys
sys.path.insert(0, sys.argv[1])
import _transaction as tx
import install
phase = sys.argv[2]
def crash(name):
    if name == phase: os.kill(os.getpid(), signal.SIGKILL)
tx.checkpoint = crash
sys.argv = ['install.py']
install.main()
'''
                    result = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT/'tools'), phase],
                                            env=f.env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, -9, result.stdout+result.stderr)
                    self.assertTrue((app/'manga.py').is_file())
                    result = subprocess.run([sys.executable, '-B', str(ROOT/'tools/install.py'), '--recover'],
                                            env=f.env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((app/'VERSION').read_text().strip(), '0.8.3' if phase == 'committed' else '0.6.3')
                    self.assertEqual({p.name:p.read_bytes() for p in data.iterdir()}, before)
                finally:
                    f.tearDown()

    def fake_apt_environment(self):
        fake = self.f.base/'aptbin'; fake.mkdir()
        (fake/'dirname').symlink_to(shutil.which('dirname'))
        (fake/'python3').symlink_to(sys.executable)
        (fake/'sudo').write_text('#!/bin/sh\nexec "$@"\n'); (fake/'sudo').chmod(0o755)
        (fake/'apt-get').write_text('#!/bin/sh\nprintf "called\\n" >> "$APT_LOG"\nexit 100\n')
        (fake/'apt-get').chmod(0o755)
        self.f.env.update(PATH=str(fake), APT_LOG=str(self.f.base/'apt-calls'))
        self.f.env.pop('MANGA_CLI_SKIP_SYSTEM_DEPS', None)
        return self.f.base/'apt-calls'

    def test_failed_dependency_installation_keeps_old_code_and_data(self):
        app, data = self.f.seed()
        before = {p.name:p.read_bytes() for p in data.iterdir()}
        calls = self.fake_apt_environment()
        result = self.f.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(calls.is_file())
        self.assertEqual((app/'VERSION').read_text(), '0.6.3')
        self.assertEqual({p.name:p.read_bytes() for p in data.iterdir()}, before)

    def test_corrupt_release_is_rejected_before_system_package_manager(self):
        app, _ = self.f.seed()
        clone = self.clone(); (clone/'README.md').write_text('CORRUPTED')
        calls = self.fake_apt_environment()
        result = self.f.run_install(clone)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(calls.exists())
        self.assertEqual((app/'VERSION').read_text(), '0.6.3')

    def test_readonly_code_parent_does_not_destroy_existing_installation(self):
        app, data = self.f.seed()
        parent = app.parent; old_mode = parent.stat().st_mode & 0o777
        parent.chmod(0o500)
        try:
            result = self.f.run_install()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((app/'VERSION').read_text(), '0.6.3')
        finally:
            parent.chmod(old_mode)

    def test_failed_mpv_probe_does_not_replace_old_code(self):
        app, _ = self.f.seed()
        (self.f.fakebin/'mpv').write_text('#!/bin/sh\nprintf "  x11  test output\\n"\nexit 1\n')
        result = self.f.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app/'VERSION').read_text(), '0.6.3')

    def test_failures_before_publication_keep_the_previous_installation(self):
        app, data = self.f.seed()
        before = {p.name:p.read_bytes() for p in data.iterdir()}
        for phase in ('stage_verified', 'backup_verified'):
            with self.subTest(phase=phase):
                script = '''
import errno, sys
sys.path.insert(0, sys.argv[1])
import install, _transaction as tx
phase = sys.argv[2]
def fail(name):
    if name == phase: raise OSError(errno.ENOSPC, 'simulated disk full')
tx.checkpoint = fail
sys.argv = ['install.py']
install.main()
'''
                result = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT/'tools'), phase],
                                        env=self.f.env, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((app/'VERSION').read_text(), '0.6.3')
                self.assertEqual({p.name:p.read_bytes() for p in data.iterdir()}, before)
                self.assertFalse((app.parent/'.manga-cli-transaction.json').exists())

    def test_sigint_and_sigterm_trigger_code_recovery(self):
        app, data = self.f.seed()
        before = {p.name:p.read_bytes() for p in data.iterdir()}
        for signame in ('SIGINT', 'SIGTERM'):
            with self.subTest(signal=signame):
                script = '''
import os, signal, sys
sys.path.insert(0, sys.argv[1])
import install, _transaction as tx
sig = getattr(signal, sys.argv[2])
def interrupt(name):
    if name == 'desktop': os.kill(os.getpid(), sig)
tx.checkpoint = interrupt
sys.argv = ['install.py']
install.main()
'''
                result = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT/'tools'), signame],
                                        env=self.f.env, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((app/'VERSION').read_text(), '0.6.3')
                self.assertEqual({p.name:p.read_bytes() for p in data.iterdir()}, before)
                self.assertFalse((app.parent/'.manga-cli-transaction.json').exists())

    def test_backup_helpers_come_from_verified_staging_not_later_source_edits(self):
        clone = self.clone()
        expected = (clone/'tools/restore.py').read_bytes()
        script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import install, _transaction as tx
def edit_source(name):
    if name == 'stage_verified':
        (install.ROOT/'tools/restore.py').write_text('UNVERIFIED LATER SOURCE EDIT')
tx.checkpoint = edit_source
sys.argv = ['install.py']
install.main()
"""
        result = subprocess.run([sys.executable, '-B', '-c', script, str(clone/'tools')],
                                env=self.f.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((self.f.backups()[-1]/'restore.py').read_bytes(), expected)
        self.assertEqual((self.f.home/'.local/lib/anticomadreja-manga/tools/restore.py').read_bytes(), expected)

    def test_readonly_and_recovery_options_never_bootstrap_packages(self):
        app, _ = self.f.seed()
        calls = self.fake_apt_environment()
        for option in ('--help', '--check', '--recover'):
            with self.subTest(option=option):
                result = subprocess.run(['/bin/sh', str(ROOT/'install.sh'), option],
                                        env=self.f.env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                self.assertFalse(calls.exists())
                self.assertEqual((app/'VERSION').read_text(), '0.6.3')

    def test_unknown_installer_option_is_rejected_before_package_changes(self):
        app, _ = self.f.seed()
        calls = self.fake_apt_environment()
        result = subprocess.run(['/bin/sh', str(ROOT/'install.sh'), '--unknown-option'],
                                env=self.f.env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(calls.exists())
        self.assertEqual((app/'VERSION').read_text(), '0.6.3')

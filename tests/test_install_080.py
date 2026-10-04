"""Release-specific installer checks using isolated HOME and a fake mpv probe."""
import json
import os
from pathlib import Path
import subprocess
import unittest

from tests import test_installer

ROOT = Path(__file__).resolve().parents[1]


class Install080Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_installer.InstallerTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_clean_install_creates_legacy_alias_when_name_is_free(self):
        result = self.fixture.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        alias = self.fixture.home / '.local/bin/manga'
        self.assertTrue(os.access(alias, os.X_OK))
        result = subprocess.run([str(alias), '--version'], env=self.fixture.env,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.stdout.strip(), '0.8.2')

    def test_upgrade_from_075_preserves_complete_synthetic_state_and_settings_bytes(self):
        app, data = self.fixture.seed()
        (app / 'VERSION').write_text('0.7.5')
        state = {'schema': 6, 'saved': {'test:x': {'title': 'Synthetic work'}},
                 'progress': {'test:x': {'chapter_number': '3', 'page': 6,
                                         'reader_mode': 'width', 'view': {'position': 0.63}}},
                 'last_read': 'test:x'}
        settings = {'schema': 2, 'reader_fit': 'width', 'auto_page_turn': False,
                    'remember_reader_mode': True, 'save_reader_position': True,
                    'show_page_indicator': False, 'prefetch_pages': 5,
                    'prefetch_next_chapter': False, 'scroll_step': 0.2,
                    'source_mode': 'mangapill', 'preferred_language': 'en'}
        (data / 'state.json').write_text(json.dumps(state, indent=4) + '\n')
        (data / 'settings.json').write_text(json.dumps(settings, indent=3) + '\n')
        before = {p.name: p.read_bytes() for p in data.iterdir()}
        result = self.fixture.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in data.iterdir()}, before)
        self.assertEqual((app / 'VERSION').read_text().strip(), '0.8.2')
        self.assertEqual((self.fixture.backups()[-1] / 'app/VERSION').read_text(), '0.7.5')

    def test_saved_accent_survives_reinstall(self):
        _, data = self.fixture.seed()
        (data / 'settings.json').write_text('{"schema":2,"accent_color":"blue"}\n')
        before = (data / 'settings.json').read_bytes()
        result = self.fixture.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((data / 'settings.json').read_bytes(), before)

    def test_standalone_restore_has_catalogue_and_preserves_new_preferences(self):
        app, data = self.fixture.seed()
        result = self.fixture.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = self.fixture.backups()[-1]
        self.assertTrue((backup / '_messages.py').is_file())
        (data / 'settings.json').write_text('{"accent_color":"magenta"}\n')
        before = (data / 'settings.json').read_bytes()
        result = subprocess.run([str(Path(os.sys.executable)), str(backup / 'restore.py')],
                                env=self.fixture.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Previous code restored', result.stdout)
        self.assertEqual((data / 'settings.json').read_bytes(), before)
        self.assertEqual((app / 'VERSION').read_text(), '0.6.3')

    def test_clean_install_restore_removes_only_created_launchers(self):
        result = self.fixture.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = self.fixture.backups()[-1]
        result = subprocess.run([os.sys.executable, str(backup / 'restore.py')],
                                env=self.fixture.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ('manga-cli', 'manga'):
            self.assertFalse((self.fixture.home / '.local/bin' / name).exists())

    def test_installed_package_includes_locale_license_and_docs(self):
        result = self.fixture.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        root = self.fixture.home / '.local/lib/anticomadreja-manga'
        for rel in ('acmanga/locales/en.json', 'LICENSE', 'SECURITY.md', 'CONTRIBUTING.md', 'docs/LOCALIZATION.md'):
            self.assertTrue((root / rel).is_file(), rel)

    def test_corrupt_english_catalogue_is_rejected_before_touching_old_install(self):
        import shutil
        app, data = self.fixture.seed()
        broken = self.fixture.base / 'broken'
        shutil.copytree(ROOT, broken, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (broken / 'acmanga/locales/en.json').write_text('{}')
        result = self.fixture.run_install(broken)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Integrity check failed', result.stderr)
        self.assertEqual((app / 'VERSION').read_text(), '0.6.3')

    def test_installer_and_desktop_copy_are_english(self):
        result = self.fixture.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('manga-cli 0.8.2 installed.', result.stdout)
        self.assertIn('Private backup:', result.stdout)
        text = (self.fixture.home / '.local/share/applications/manga-cli.desktop').read_text()
        self.assertIn('terminal manga reader', text)
        self.assertNotIn('lector de terminal', text)

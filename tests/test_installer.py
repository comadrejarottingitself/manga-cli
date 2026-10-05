import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.home=self.base/'home';self.home.mkdir();self.fakebin=self.base/'bin';self.fakebin.mkdir()
        mpv=self.fakebin/'mpv'
        mpv.write_text('#!/bin/sh\nprintf "  x11  X11 software video output\\n"\n')
        mpv.chmod(0o755)
        self.env=os.environ.copy();self.env['HOME']=str(self.home);self.env['PATH']=str(self.fakebin)+os.pathsep+self.env['PATH']
        self.env['PYTHONDONTWRITEBYTECODE']='1'
        for key in ['XDG_DATA_HOME','XDG_CACHE_HOME','SUDO_USER']:self.env.pop(key,None)
    def tearDown(self):self.tmp.cleanup()
    def run_install(self,root=ROOT):
        return subprocess.run(['/bin/sh',str(root/'install.sh')],env=self.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    def data(self):return Path(self.env.get('XDG_DATA_HOME',str(self.home/'.local/share')))/'anticomadreja-manga'
    def backups(self):return sorted((self.data().parent/'anticomadreja-manga-backups').iterdir())
    def seed(self):
        data=self.data();data.mkdir(parents=True)
        (data/'state.json').write_text('{"schema":6,"saved":{},"progress":{},"last_read":null}\n')
        (data/'settings.json').write_text('{"schema":2,"source_mode":"auto"}\n')
        app=self.home/'.local/lib/anticomadreja-manga';app.mkdir(parents=True)
        (app/'VERSION').write_text('0.6.3');(app/'manga.py').write_text('# old code')
        legacy=self.home/'.local/bin/manga';legacy.parent.mkdir(parents=True)
        legacy.write_text('#!/bin/sh\nexec python3 "$HOME/.local/lib/anticomadreja-manga/manga.py" "$@"\n');legacy.chmod(0o755)
        return app,data

    def test_install_exposes_new_command_and_menu(self):
        result=self.run_install();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        launcher=self.home/'.local/bin/manga-cli';self.assertTrue(os.access(launcher,os.X_OK))
        check=subprocess.run([str(launcher),'--version'],env=self.env,text=True,stdout=subprocess.PIPE)
        self.assertEqual(check.stdout.strip(),'0.8.3')
        self.assertTrue((self.home/'.local/share/applications/manga-cli.desktop').is_file())

    def test_upgrade_preserves_private_data_byte_for_byte(self):
        app,data=self.seed();before={p.name:p.read_bytes() for p in data.iterdir()}
        result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual({p.name:p.read_bytes() for p in data.iterdir()},before)
        self.assertEqual((app/'VERSION').read_text().strip(),'0.8.3')
        backup=self.backups()[-1]
        self.assertEqual((backup/'app/VERSION').read_text(),'0.6.3')
        self.assertEqual((backup/'data/state.json').read_bytes(),before['state.json'])
        self.assertEqual(backup.stat().st_mode & 0o777,0o700)

    def test_old_manga_command_becomes_owned_alias(self):
        self.seed();result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        legacy=self.home/'.local/bin/manga'
        self.assertIn('manga-cli managed launcher',legacy.read_text())
        run=subprocess.run([str(legacy),'--version'],env=self.env,text=True,stdout=subprocess.PIPE)
        self.assertEqual(run.stdout.strip(),'0.8.3')

    def test_install_adds_managed_bash_path_block_and_new_terminal_finds_command(self):
        self.env['SHELL']='/bin/bash'
        bashrc=self.home/'.bashrc';bashrc.write_text('# existing user config\n')
        result=self.run_install();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        text=bashrc.read_text()
        self.assertIn('# existing user config',text)
        self.assertIn('# >>> manga-cli PATH >>>',text)
        self.assertEqual(text.count('# >>> manga-cli PATH >>>'),1)
        probe=subprocess.run(['/bin/bash','--noprofile','--rcfile',str(bashrc),'-ic','command -v manga-cli'],
                             env=self.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        self.assertEqual(probe.returncode,0,probe.stderr)
        self.assertEqual(probe.stdout.strip(),str(self.home/'.local/bin/manga-cli'))

    def test_reinstall_does_not_duplicate_bash_path_block(self):
        self.env['SHELL']='/bin/bash'
        for _ in range(2):
            result=self.run_install();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        text=(self.home/'.bashrc').read_text()
        self.assertEqual(text.count('# >>> manga-cli PATH >>>'),1)

    def test_uninstall_removes_only_managed_bash_path_block(self):
        self.env['SHELL']='/bin/bash'
        bashrc=self.home/'.bashrc';bashrc.write_text('# keep me\n')
        result=self.run_install();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=subprocess.run(['/bin/sh',str(ROOT/'uninstall.sh')],env=self.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('# keep me',bashrc.read_text())
        self.assertNotIn('# >>> manga-cli PATH >>>',bashrc.read_text())

    def test_unrelated_manga_command_not_overwritten(self):
        legacy=self.home/'.local/bin/manga';legacy.parent.mkdir(parents=True);legacy.write_text('# unrelated program\n')
        result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(legacy.read_text(),'# unrelated program\n')

    def test_conflicting_manga_cli_refused(self):
        launcher=self.home/'.local/bin/manga-cli';launcher.parent.mkdir(parents=True);launcher.write_text('unrelated\n')
        result=self.run_install();self.assertNotEqual(result.returncode,0)
        self.assertEqual(launcher.read_text(),'unrelated\n')

    def test_corrupt_package_does_not_touch_installation(self):
        app,data=self.seed();broken=self.base/'broken'
        shutil.copytree(ROOT,broken,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        with (broken/'acmanga/reader.lua').open('a') as f:f.write('\n-- corrupted\n')
        result=self.run_install(broken);self.assertNotEqual(result.returncode,0)
        self.assertEqual((app/'VERSION').read_text(),'0.6.3')
        self.assertIn('Integrity',result.stderr)

    def test_restore_keeps_new_progress(self):
        app,data=self.seed();result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        (data/'state.json').write_text('CURRENT PROGRESS')
        undo=subprocess.run(['python3',str(self.backups()[-1]/'restore.py')],env=self.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(undo.returncode,0,undo.stderr)
        self.assertEqual((app/'VERSION').read_text(),'0.6.3')
        self.assertEqual((data/'state.json').read_text(),'CURRENT PROGRESS')

    def test_xdg_data_location_is_respected(self):
        self.env['XDG_DATA_HOME']=str(self.home/'custom-data')
        app,data=self.seed();result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(self.backups())
        self.assertTrue((self.home/'custom-data/applications/manga-cli.desktop').is_file())
        self.assertFalse((self.home/'.local/share/anticomadreja-manga').exists())

    def test_repeated_install_keeps_separate_backups(self):
        self.seed()
        for _ in range(2):
            result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(self.backups()),2)

    def test_no_purge_of_existing_cache(self):
        cache=self.home/'.cache/anticomadreja-manga';cache.mkdir(parents=True);(cache/'test').write_text('keep')
        result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((cache/'test').read_text(),'keep')

    def test_uninstall_keeps_progress(self):
        _,data=self.seed();result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        raw=(data/'state.json').read_bytes()
        result=subprocess.run(['/bin/sh',str(ROOT/'uninstall.sh')],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((data/'state.json').read_bytes(),raw)
        self.assertFalse((self.home/'.local/bin/manga-cli').exists())

    def test_upgrade_from_074_keeps_mode_and_controls_preferences_unchanged(self):
        app,data=self.seed()
        (app/'VERSION').write_text('0.7.4')
        (data/'state.json').write_text('{"schema":6,"saved":{},"progress":{"mangakatana:x":{"title":"X","page":7,"chapter_number":"10","view":{"fit":"width","position":0.6}}},"last_read":"mangakatana:x"}')
        (data/'settings.json').write_text('{"schema":2,"reader_fit":"page","prefetch_pages":5,"source_mode":"auto"}')
        before={p.name:p.read_bytes() for p in data.iterdir()}
        result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual({p.name:p.read_bytes() for p in data.iterdir()},before)
        self.assertEqual((self.backups()[-1]/'app/VERSION').read_text(),'0.7.4')

    def test_does_not_alter_global_mpv_or_ani_cli_files(self):
        paths=[self.home/'.config/mpv/mpv.conf',self.home/'.config/mpv/input.conf',
               self.home/'.local/bin/ani-cli']
        for path in paths:
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('KEEP ORIGINAL '+path.name)
        before={p:p.read_bytes() for p in paths}
        result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual({p:p.read_bytes() for p in paths},before)

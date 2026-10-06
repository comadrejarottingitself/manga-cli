"""Negative packaging tests: bad paths, hidden files, damaged manifests and I/O."""
from contextlib import redirect_stdout
import errno
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT/'tools') not in sys.path:
    sys.path.insert(0, str(ROOT/'tools'))
import _release as release
import build_release as build
import privacy_check as privacy


class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'source'
        self.root.mkdir()
        (self.root/'VERSION').write_text('0.8.1\n')
        (self.root/'README.md').write_text('Public example project\n')
        self.approve(['VERSION', 'README.md', 'release-files.txt'])
        self.patch = mock.patch.object(build, 'ROOT', self.root)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def approve(self, names):
        (self.root/'release-files.txt').write_text('\n'.join(sorted(names))+'\n')
        (self.root/'SHA256SUMS').write_bytes(release.manifest_bytes(release.read_payload(self.root)))

    def privacy_result(self):
        output = io.StringIO()
        with mock.patch.object(privacy, 'ROOT', self.root), redirect_stdout(output):
            result = privacy.main()
        return result, output.getvalue()

    def test_existing_unrelated_output_contents_survive(self):
        out = self.base/'output'; out.mkdir()
        sentinel = out/'unrelated.txt'; sentinel.write_bytes(b'KEEP')
        archive, checksum = build.build_archive(out)
        self.assertEqual(sentinel.read_bytes(), b'KEEP')
        self.assertTrue(archive.is_file() and checksum.is_file())

    def test_unsafe_output_paths_are_refused(self):
        for path in [self.root, self.base, Path('/'), Path.home(), self.root/'docs']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                build.destination(path)
        self.assertEqual((self.root/'README.md').read_text(), 'Public example project\n')

    def test_output_directory_symlink_is_refused(self):
        actual = self.base/'actual'; actual.mkdir()
        link = self.base/'link'; link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError):
            build.build_archive(link)
        self.assertEqual(list(actual.iterdir()), [])

    def test_output_parent_symlink_is_refused(self):
        actual = self.base/'actual'; actual.mkdir()
        link = self.base/'link'; link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError):
            build.destination(link/'nested')

    def test_symlink_archive_cannot_overwrite_its_destination(self):
        out = self.base/'out'; out.mkdir()
        sentinel = self.base/'sentinel'; sentinel.write_bytes(b'KEEP')
        (out/'manga-cli-0.8.1_Debian12.zip').symlink_to(sentinel)
        with self.assertRaises(ValueError):
            build.build_archive(out)
        self.assertEqual(sentinel.read_bytes(), b'KEEP')

    def test_symlink_checksum_is_refused_before_archive_write(self):
        out = self.base/'out'; out.mkdir()
        sentinel = self.base/'sentinel'; sentinel.write_bytes(b'KEEP')
        (out/'manga-cli-0.8.1_Debian12.zip.sha256').symlink_to(sentinel)
        with self.assertRaises(ValueError):
            build.build_archive(out)
        self.assertFalse((out/'manga-cli-0.8.1_Debian12.zip').exists())
        self.assertEqual(sentinel.read_bytes(), b'KEEP')

    def test_default_dist_is_allowed_without_deleting_existing_files(self):
        dist = self.root/'dist'; dist.mkdir()
        (dist/'keep.txt').write_text('KEEP')
        build.build_archive(dist)
        self.assertEqual((dist/'keep.txt').read_text(), 'KEEP')

    def test_repeated_builds_have_identical_bytes(self):
        first, _ = build.build_archive(self.base/'one')
        second, _ = build.build_archive(self.base/'two')
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_injected_write_failure_keeps_old_zip(self):
        out = self.base/'out'
        archive, _ = build.build_archive(out)
        before = archive.read_bytes()
        with mock.patch.object(zipfile.ZipFile, 'writestr', side_effect=OSError(errno.ENOSPC, 'simulated full disk')):
            with self.assertRaises(OSError):
                build.build_archive(out)
        self.assertEqual(archive.read_bytes(), before)
        self.assertFalse(list(out.glob('.manga-cli-archive-*')))

    def test_zip_contains_only_approved_files_and_exact_manifest(self):
        (self.root/'.env.local').write_text('SYNTHETIC')
        (self.root/'logs').mkdir(); (self.root/'logs/debug.log').write_text('SYNTHETIC')
        (self.root/'backups').mkdir(); (self.root/'backups/local-note.txt').write_text('SYNTHETIC')
        self.assertEqual(build.release_files(), release.release_paths(self.root))
        self.assertFalse(any(p.name in {'.env.local', 'debug.log', 'local-note.txt'} for p in build.release_files()))
        archive, _ = build.build_archive(self.base/'out')
        with zipfile.ZipFile(archive) as z:
            names = {n.split('/', 1)[1] for n in z.namelist()}
            self.assertEqual(names, {'VERSION', 'README.md', 'release-files.txt', 'SHA256SUMS'})
            self.assertEqual(z.read('manga-cli-0.8.1/SHA256SUMS'), (self.root/'SHA256SUMS').read_bytes())
        # A normal command also fails the privacy guard instead of ignoring these files.
        result, _ = self.privacy_result()
        self.assertEqual(result, 1)

    def test_allowlisted_private_filename_still_fails_privacy(self):
        (self.root/'state.json').write_text('{}')
        self.approve(['VERSION', 'README.md', 'release-files.txt', 'state.json'])
        self.assertEqual(self.privacy_result()[0], 1)

    def test_privacy_covers_ignored_file_families_and_unknown_extensions(self):
        names = ['.env', '.env.local', '.env.production', '.envrc', 'logs/debug.log',
                 'backups/local-note.txt', 'nested/backups-copy/note.txt', 'settings.json',
                 'history.json', 'progress.json', 'secrets.key', 'data.sqlite3', 'private.unknown']
        for name in names:
            with self.subTest(name=name):
                path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('SYNTHETIC PRIVATE CONTENT')
                self.assertEqual(self.privacy_result()[0], 1)
                path.unlink()
        self.assertEqual(self.privacy_result()[0], 0)

    def test_privacy_redacts_secret_values(self):
        token = 'ghp_' + 'Z'*30
        (self.root/'README.md').write_text(token)
        result, output = self.privacy_result()
        self.assertEqual(result, 1)
        self.assertNotIn(token, output)

    def test_symlinked_payload_is_rejected(self):
        (self.root/'README.md').unlink()
        victim = self.base/'outside'; victim.write_text('PUBLIC')
        (self.root/'README.md').symlink_to(victim)
        with self.assertRaises(ValueError):
            release.verified_payload(self.root)
        self.assertEqual(self.privacy_result()[0], 1)

    def test_hardlinked_payload_is_rejected(self):
        if not hasattr(os, "link"):
            self.skipTest("hard links are unavailable on this platform")
        try:
            os.link(self.root/'README.md', self.base/'hardlink')
        except OSError as exc:
            self.skipTest("filesystem cannot create hard links here: {}".format(exc))
        with self.assertRaises(ValueError):
            release.read_payload(self.root)

    def test_manifest_requires_complete_exact_coverage(self):
        manifest = self.root/'SHA256SUMS'
        good = manifest.read_bytes()
        for bad in [b'', good.splitlines(keepends=True)[0], good+good, good.replace(b'README.md', b'../README.md')]:
            with self.subTest(bad=bad[:12]):
                manifest.write_bytes(bad)
                with self.assertRaises(RuntimeError):
                    release.verified_payload(self.root)
        manifest.write_bytes(good)
        release.verified_payload(self.root)

    def test_allowlist_rejects_traversal_duplicate_and_absolute_paths(self):
        good = (self.root/'release-files.txt').read_text()
        for name in ('../outside', '/absolute', 'a/../x', 'a\\x', 'README.md'):
            with self.subTest(name=name):
                (self.root/'release-files.txt').write_text('\n'.join(sorted(good.splitlines()+[name]))+'\n')
                with self.assertRaises((ValueError, OSError)):
                    release.release_paths(self.root)
        (self.root/'release-files.txt').write_text(good)

    def test_forged_version_cannot_change_output_location(self):
        (self.root/'VERSION').write_text('../../outside')
        self.approve(['VERSION', 'README.md', 'release-files.txt'])
        with self.assertRaises(ValueError):
            build.build_archive(self.base/'out')
        self.assertFalse((self.base/'out').exists())

    def test_manifest_atomic_write_error_preserves_previous_file(self):
        path = self.root/'SHA256SUMS'; before = path.read_bytes()
        with mock.patch.object(release.os, 'fsync', side_effect=OSError(errno.ENOSPC, 'full')):
            with self.assertRaises(OSError):
                release.atomic_write(path, b'bad')
        self.assertEqual(path.read_bytes(), before)

    def test_unlisted_file_never_becomes_payload_even_inside_tools(self):
        (self.root/'tools').mkdir()
        (self.root/'tools/local.py').write_text('private = True')
        self.assertNotIn(Path('tools/local.py'), build.release_files())
        self.assertEqual(self.privacy_result()[0], 1)

    def test_png_metadata_and_hidden_trailing_content_are_rejected(self):
        import struct
        import zlib
        def chunk(kind, content):
            return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', zlib.crc32(kind+content) & 0xffffffff)
        image = self.root/'example.png'
        # Small synthetic structural fixtures, not user screenshots.
        base = b'\x89PNG\r\n\x1a\n'
        image.write_bytes(base + chunk(b'tEXt', b'Author\0Synthetic') + chunk(b'IEND', b''))
        self.assertIn('PNG metadata chunk', privacy.check_image_metadata(image))
        image.write_bytes(base + chunk(b'IEND', b'') + b'PRIVATE TRAILING CONTENT')
        self.assertIn('unexpected data after PNG end', privacy.check_image_metadata(image))
        image.write_bytes(b'not an image')
        self.assertIn('invalid PNG image', privacy.check_image_metadata(image))

    def test_source_symlink_directory_is_reported_not_followed(self):
        outside = self.base/'outside'; outside.mkdir()
        (outside/'private.txt').write_text('SYNTHETIC')
        (self.root/'linked').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.privacy_result()[0], 1)
        self.assertEqual((outside/'private.txt').read_text(), 'SYNTHETIC')

    def git(self, *args):
        import subprocess
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)

    def test_git_guard_checks_force_added_ignored_files(self):
        (self.root/'.gitignore').write_text('dist/\n')
        self.approve(['.gitignore', 'VERSION', 'README.md', 'release-files.txt'])
        self.git('init', '-q')
        self.git('add', '.')
        with mock.patch.object(privacy, 'ROOT', self.root):
            self.assertEqual(privacy.check_git_index(), [])
        (self.root/'dist').mkdir()
        (self.root/'dist/.env.local').write_text('SYNTHETIC PRIVATE CONTENT')
        self.git('add', '-f', 'dist/.env.local')
        with mock.patch.object(privacy, 'ROOT', self.root):
            findings = privacy.check_git_index()
        self.assertTrue(any(name == 'dist/.env.local' for name, _ in findings))

    def test_git_guard_checks_staged_bytes_not_just_working_copy(self):
        self.git('init', '-q')
        original = (self.root/'README.md').read_bytes()
        (self.root/'README.md').write_text('STAGED SYNTHETIC PRIVATE VALUE')
        self.git('add', '.')
        (self.root/'README.md').write_bytes(original)
        with mock.patch.object(privacy, 'ROOT', self.root):
            findings = privacy.check_git_index()
        self.assertTrue(any('staged bytes differ' in finding for _, finding in findings))

#!/usr/bin/env python3
"""Build an allowlisted, reproducible ZIP without deleting output directories."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import zipfile

from _release import (VERSION_RE, atomic_write, manifest_bytes, read_payload,
                      release_paths, verified_payload)

ROOT = Path(__file__).resolve().parents[1]
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def release_files():
    return release_paths(ROOT)


def manifest_text(files):
    return manifest_bytes({p.as_posix(): (ROOT/p).read_bytes() for p in files}).decode('utf-8')


def destination(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    # Never follow symlinks in an output path, including its existing parents.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Release output must not contain symbolic links')
    root = ROOT.resolve()
    if path == Path.home().resolve() or path == root or path in root.parents:
        raise ValueError('Refusing HOME, source or an ancestor as the release output')
    if root in path.parents and path != root/'dist':
        raise ValueError('Inside the source tree, only the dedicated dist directory is supported')
    if path.exists() and not path.is_dir():
        raise ValueError('Release output is not a directory')
    return path


def run_checks(tests=False):
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    commands = [[sys.executable, str(ROOT/'tools/privacy_check.py')],
                [sys.executable, str(ROOT/'tools/sync_text.py'), '--check']]
    if tests:
        commands += [[sys.executable, '-m', 'unittest', 'discover', '-v'],
                     [sys.executable, str(ROOT/'manga.py'), '--self-test']]
    for command in commands:
        subprocess.run(command, cwd=ROOT, env=env, check=True)


def build_archive(output_dir: Path, files=None):
    output_dir = destination(output_dir)
    payload = verified_payload(ROOT)  # Read once: ZIP and manifest use these exact bytes.
    version = payload['VERSION'].decode('utf-8').strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError('Invalid release version')
    payload['SHA256SUMS'] = manifest_bytes(payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir/('manga-cli-' + version + '_Debian12.zip')
    checksum = archive.with_suffix('.zip.sha256')
    for path in (archive, checksum):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('Release output file is a link or non-file: ' + path.name)
    lockfd = os.open(output_dir/'.manga-cli-build.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lockfd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            raise ValueError('Unsafe release build lock file')
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd, name = tempfile.mkstemp(prefix='.manga-cli-archive-', dir=output_dir)
        temp = Path(name)
        try:
            with os.fdopen(fd, 'w+b') as handle:
                with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                    for rel, data in sorted(payload.items()):
                        info = zipfile.ZipInfo('manga-cli-' + version + '/' + rel, ZIP_TIME)
                        info.create_system = 3
                        info.external_attr = (0o100755 if rel in ('install.sh', 'uninstall.sh') else 0o100644) << 16
                        info.compress_type = zipfile.ZIP_DEFLATED
                        z.writestr(info, data)
                handle.flush()
                os.fsync(handle.fileno())
            with zipfile.ZipFile(temp) as z:
                if z.testzip():
                    raise RuntimeError('ZIP self-verification failed')
            digest = hashlib.sha256(temp.read_bytes()).hexdigest()
            os.chmod(temp, 0o644)
            os.replace(temp, archive)
            # A crash between these two commits leaves a detectable checksum mismatch;
            # rerunning the builder repairs it. Neither commit removes unrelated files.
            atomic_write(checksum, (digest + '  ' + archive.name + '\n').encode('ascii'))
        finally:
            temp.unlink(missing_ok=True)
    finally:
        os.close(lockfd)  # Do not unlink a live lock inode.
    return archive, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='verify the existing manifest without rewriting it')
    parser.add_argument('--skip-tests', action='store_true', help='skip tests, not privacy/text/integrity checks')
    parser.add_argument('--output-dir', default=str(ROOT/'dist'))
    args = parser.parse_args()
    output = destination(Path(args.output_dir)) if not args.check else None
    run_checks()
    if args.check:
        payload = verified_payload(ROOT)
        print('SHA256SUMS OK ({} payload files).'.format(len(payload)))
        return 0
    atomic_write(ROOT/'SHA256SUMS', manifest_bytes(read_payload(ROOT)))
    if not args.skip_tests:
        run_checks(tests=True)
    # Do not regenerate the manifest here: a source change during testing must fail.
    archive, checksum = build_archive(output)
    print('Built ' + str(archive))
    print('Built ' + str(checksum))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)

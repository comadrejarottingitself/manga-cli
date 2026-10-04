"""Reviewed payload selection and integrity checks, shared by release/setup tools."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

ALLOWLIST = 'release-files.txt'
VERSION_RE = re.compile(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?\Z')


def safe_relative(name: str) -> Path:
    parts = PurePosixPath(name).parts
    if (not name or name.startswith('/') or '\\' in name or not parts
            or any(part in ('', '.', '..') for part in name.split('/'))
            or any(ord(c) < 32 or ord(c) > 126 for c in name)
            or ':' in name or name != PurePosixPath(name).as_posix()):
        raise ValueError('Unsafe package path')
    return Path(*parts)


def regular_path(root: Path, rel: Path) -> Path:
    """Refuse links (including ancestor links), hardlinks and special files."""
    path = root
    for part in rel.parts:
        path = path / part
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise ValueError('Symbolic links are not allowed in the payload: ' + str(rel))
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise ValueError('Expected a regular, unlinked payload file: ' + str(rel))
    return path


def release_paths(root: Path) -> list[Path]:
    lines = regular_path(root, Path(ALLOWLIST)).read_text(encoding='utf-8').splitlines()
    names = [line for line in lines if line and not line.startswith('#')]
    if len(names) != len(set(names)) or names != sorted(names):
        raise ValueError('Release allowlist must be sorted with no duplicates')
    if ALLOWLIST not in names or 'SHA256SUMS' in names or 'VERSION' not in names:
        raise ValueError('Release allowlist is incomplete or includes its checksum manifest')
    paths = [safe_relative(name) for name in names]
    for path in paths:
        regular_path(root, path)
    return paths


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_bytes(payload: dict[str, bytes]) -> bytes:
    return ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                   for name, data in sorted(payload.items())).encode('utf-8')


def read_payload(root: Path) -> dict[str, bytes]:
    return {rel.as_posix(): regular_path(root, rel).read_bytes() for rel in release_paths(root)}


def verified_payload(root: Path) -> dict[str, bytes]:
    """Exact coverage, not just verification of whatever entries a manifest contains."""
    payload = read_payload(root)
    actual = regular_path(root, Path('SHA256SUMS')).read_bytes()
    if actual != manifest_bytes(payload):
        raise RuntimeError('Integrity check failed: SHA256SUMS does not match the complete approved payload')
    if not VERSION_RE.fullmatch(payload['VERSION'].decode('utf-8').strip()):
        raise ValueError('Invalid release version')
    return payload


def atomic_write(path: Path, content: bytes, mode: int = 0o644) -> None:
    """Write one file without truncating the previous file on a write failure."""
    import tempfile
    fd, name = tempfile.mkstemp(prefix='.manga-cli-write-', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(content)
            out.flush()
            os.fchmod(out.fileno(), mode)
            os.fsync(out.fileno())
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('Refusing to replace a link or non-file: ' + path.name)
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)

#!/usr/bin/env python3
"""Fail if the source tree contains common private-data or secret patterns.

This is a release guardrail, not a replacement for reviewing staged Git files and
commit metadata before publishing.
"""
from __future__ import annotations

import re
import struct
import os
import stat
import zlib
from _release import release_paths
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Development-only directories are never approved release payloads.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv", "dist", "build"}
FORBIDDEN_NAMES = {"state.json", "settings.json", "history.json", "progress.json", "control.json", "timings.json", "manifest.json", "pages.json", "pages.m3u", "input.conf", ".env", ".envrc", "id_rsa", "id_ed25519"}
FORBIDDEN_DIRS = {"logs", "backups", "cache", "caches", ".cache", ".local"}

TOKEN_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("OpenAI-style token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("credentialed URL", re.compile(r"https?://[^\s/:]+:[^\s/@]+@")),
]
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
UNIX_HOME_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/([^/\s'\"]+)")
WINDOWS_HOME_RE = re.compile(r"\b[A-Za-z]:\\Users\\([^\\\s'\"]+)", re.IGNORECASE)
PRIVATE_IP_RE = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)
SAFE_USERS = {"example", "user", "test", "runner", "secret"}
SAFE_EMAIL_DOMAINS = {"example.com", "users.noreply.github.com"}


def iter_files():
    approved = {p.as_posix() for p in release_paths(ROOT)} | {"SHA256SUMS"}
    for base, directories, files in os.walk(ROOT, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in SKIP_DIRS)
        for name in directories[:]:
            path = Path(base)/name
            if path.is_symlink():
                yield path, "symbolic link directory"
                directories.remove(name)
        for name in sorted(files):
            path = Path(base)/name
            rel = path.relative_to(ROOT)
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_nlink != 1:
                yield path, "link or special file"
            elif (name in FORBIDDEN_NAMES or name.startswith('.env.')
                  or any(part.lower() in FORBIDDEN_DIRS or 'backups' in part.lower() for part in rel.parts)
                  or path.suffix.lower() in {'.key', '.pem', '.log', '.bak', '.sqlite', '.sqlite3', '.zip'}):
                yield path, "private/runtime filename"
            elif rel.as_posix() not in approved:
                yield path, "file not present in reviewed release allowlist"
            else:
                yield path, None


def check_text(path: Path, text: str):
    findings = []
    for label, pattern in TOKEN_PATTERNS:
        if pattern.search(text):
            findings.append(label)

    for email in EMAIL_RE.findall(text):
        domain = email.rsplit("@", 1)[1].lower()
        if domain not in SAFE_EMAIL_DOMAINS:
            findings.append("non-example email address")

    for pattern in (UNIX_HOME_RE, WINDOWS_HOME_RE):
        for match in pattern.finditer(text):
            user = match.group(1).lower()
            if user not in SAFE_USERS:
                findings.append("user-specific home path")

    for ip in PRIVATE_IP_RE.findall(text):
        findings.append("private network address")
    return findings



def check_image_metadata(path: Path):
    """Inspect structure/metadata, not pixels; screenshots also need visual review."""
    data = path.read_bytes()
    findings = []
    if path.suffix.lower() == '.png':
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return ["invalid PNG image"]
        offset = 8
        ended = False
        while offset + 12 <= len(data):
            length = struct.unpack('>I', data[offset:offset+4])[0]
            kind = data[offset+4:offset+8]
            end = offset + length + 12
            if end > len(data):
                return findings + ["truncated PNG chunk"]
            expected_crc = struct.unpack('>I', data[end-4:end])[0]
            if zlib.crc32(data[offset+4:end-4]) & 0xffffffff != expected_crc:
                return findings + ["invalid PNG checksum"]
            if kind in {b'tEXt', b'zTXt', b'iTXt', b'eXIf'}:
                findings.append("PNG metadata chunk")
            offset = end
            if kind == b'IEND':
                ended = True
                if length or offset != len(data):
                    findings.append("unexpected data after PNG end")
                break
        if not ended:
            findings.append("missing PNG end")
    elif path.suffix.lower() in {'.jpg', '.jpeg'}:
        if not data.startswith(b'\xff\xd8'):
            return ["invalid JPEG image"]
        # APP1 covers EXIF and XMP, APP13 IPTC/Photoshop data, COM user comments.
        offset = 2
        while offset < len(data):
            if data[offset] != 255:
                findings.append("invalid JPEG marker")
                break
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                findings.append("truncated JPEG marker")
                break
            marker = data[offset]; offset += 1
            if marker in (0xda, 0xd9):
                break
            if offset + 2 > len(data):
                findings.append("truncated JPEG segment")
                break
            length = int.from_bytes(data[offset:offset+2], 'big')
            if length < 2 or offset + length > len(data):
                findings.append("invalid JPEG segment")
                break
            if marker in (0xe1, 0xed, 0xfe):
                findings.append("JPEG EXIF/XMP/IPTC/comment metadata")
            offset += length
        if not data.endswith(b'\xff\xd9'):
            findings.append("JPEG has no clean end marker")
    return findings


def check_git_index():
    """Verify staged paths AND staged bytes, including force-added ignored files."""
    result = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if result.returncode or Path(result.stdout.strip()).resolve() != ROOT.resolve():
        return [("Git index", "run this check inside the project's own initialized repository")]
    result = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '--stage', '-z'], capture_output=True, check=True)
    expected = {p.as_posix() for p in release_paths(ROOT)} | {'SHA256SUMS'}
    seen = set()
    findings = []
    for entry in result.stdout.split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode('ascii').split()
        name = raw_name.decode('utf-8')
        seen.add(name)
        if name not in expected or mode not in {'100644', '100755'} or stage != '0':
            findings.append((name, "unapproved file, link, submodule or merge conflict staged in Git"))
            continue
        data = subprocess.run(['git', '-C', str(ROOT), 'cat-file', 'blob', oid], capture_output=True, check=True).stdout
        if data != (ROOT/name).read_bytes():
            findings.append((name, "staged bytes differ from the reviewed working file; review and restage"))
    if expected - seen:
        findings.append(("Git index", "approved release files are missing from staging"))
    return findings


def main(check_git=False):
    findings = []
    try:
        candidates = list(iter_files())
    except (OSError, ValueError) as error:
        print("Privacy check FAILED: approved payload is missing, linked or invalid.")
        return 1
    for path, direct in candidates:
        rel = path.relative_to(ROOT)
        if direct:
            findings.append((str(rel), direct))
            continue
        if path.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
            for finding in check_image_metadata(path):
                findings.append((str(rel), finding))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            findings.append((str(rel), "unreadable or unexpected binary file"))
            continue
        for finding in check_text(path, text):
            findings.append((str(rel), finding))
    if check_git:
        try:
            findings.extend(check_git_index())
        except (OSError, ValueError, subprocess.SubprocessError):
            findings.append(("Git index", "unable to verify staged files"))
    if findings:
        print("Privacy check FAILED (values are deliberately redacted):")
        for rel, finding in findings:
            print("  - {}: {}".format(rel, finding))
        return 1
    print("Privacy check OK: reviewed payload only; no common secrets, personal paths or image metadata found.")
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--git-index', action='store_true', help='also require an exact, byte-matching reviewed Git index')
    args = parser.parse_args()
    sys.exit(main(check_git=args.git_index))

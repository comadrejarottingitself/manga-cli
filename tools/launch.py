#!/usr/bin/env python3
"""Hold a shared installation lock for the whole reader process lifetime."""
from __future__ import annotations

import fcntl
import os
import stat
import sys

import _transaction as tx


def main():
    paths = tx.Paths()
    tx.no_links(paths.lock)
    fd = os.open(paths.lock, os.O_RDONLY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise RuntimeError('Unsafe installation lock.')
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Installation is being changed. Open manga-cli after it completes.') from None
    if paths.journal.exists() or paths.journal.is_symlink():
        raise RuntimeError('An installation was interrupted. Run bash install.sh --recover from the release folder first.')
    os.set_inheritable(fd, True)
    os.execv(sys.executable, [sys.executable, str(paths.app/'manga.py'), *sys.argv[1:]])


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)

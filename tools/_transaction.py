"""Linux per-user file transactions used by install, recovery and uninstall.

New objects are fully written and fsynced before publication. RENAME_EXCHANGE
preserves the previous directory until commit; a durable journal resolves process
interruption. This cannot protect against failing storage or a hostile same-UID
process. No transaction ever targets reading data or caches.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import tempfile

APP = 'anticomadreja-manga'
MARKER = '# manga-cli managed launcher'
KEYS = ('app', 'launcher', 'legacy', 'desktop')


def checkpoint(name):
    """No-op seam for deterministic failure-injection tests; no environment hooks."""


def require_user():
    if os.environ.get('SUDO_USER') or os.geteuid() == 0:
        raise RuntimeError('Run this command as your normal user, without sudo or root.')


def no_links(path: Path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RuntimeError('Refusing a symbolic link in an installation path: ' + str(part))


def absolute(value: str) -> Path:
    path = Path(value)
    if not value or not path.is_absolute() or any(ord(c) < 32 for c in value) or '..' in path.parts:
        raise RuntimeError('HOME and XDG data paths must be absolute, nonempty paths without control characters.')
    no_links(path)
    return path


class Paths:
    def __init__(self):
        self.home = absolute(str(Path.home()))
        self.data_home = absolute(os.environ.get('XDG_DATA_HOME', str(self.home/'.local/share')))
        self.app = self.home/'.local/lib'/APP
        self.launcher = self.home/'.local/bin/manga-cli'
        self.legacy = self.home/'.local/bin/manga'
        self.desktop = self.data_home/'applications/manga-cli.desktop'
        self.data = self.data_home/APP
        self.backups = self.data_home/(APP+'-backups')
        self.journal = self.app.parent/'.manga-cli-transaction.json'
        self.lock = self.app.parent/'.manga-cli-install.lock'
        self.targets = {key: getattr(self, key) for key in KEYS}
        for path in (self.app.parent, self.launcher.parent, self.desktop.parent, self.data, self.backups):
            no_links(path)
        # Ambiguous aliasing of code and data is unsafe even with unusual XDG values.
        locations = [self.app, self.data, self.backups, self.launcher.parent, self.desktop.parent]
        for index, left in enumerate(locations):
            for right in locations[index+1:]:
                if left == right or left in right.parents or right in left.parents:
                    raise RuntimeError('Installation, data and backup locations overlap.')


def sync_dir(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_tree(path: Path):
    if path.is_symlink():
        return  # The containing directory persists the link, never its destination.
    if path.is_dir():
        for item in path.iterdir():
            sync_tree(item)
        sync_dir(path)
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path: Path, value: dict):
    fd, name = tempfile.mkstemp(prefix='.manga-cli-json-', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out, sort_keys=True, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        no_links(path)
        os.replace(temp, path)
        sync_dir(path.parent)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def install_lock(paths: Paths):
    paths.app.parent.mkdir(parents=True, exist_ok=True)
    no_links(paths.lock)
    fd = os.open(paths.lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            raise RuntimeError('Unsafe installation lock file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another install, restore or uninstall is running. No files were replaced.') from None
        yield
    finally:
        os.close(fd)  # Never unlink the lock: other processes may hold its inode.


@contextmanager
def handle_signals():
    previous = {}
    def interrupted(signum, frame):
        raise InterruptedError('Installation interrupted; recovery will preserve reading data.')
    for sig in (signal.SIGTERM, signal.SIGHUP):
        previous[sig] = signal.signal(sig, interrupted)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def identity(path: Path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return [info.st_dev, info.st_ino]


def rename_atomic(left: Path, right: Path, exchange=False):
    """Fail safely instead of falling back to delete-then-copy on old filesystems."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        fn = libc.renameat2
    except AttributeError:
        raise RuntimeError('Atomic directory replacement is unavailable on this system; nothing was deleted.') from None
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    # AT_FDCWD, RENAME_EXCHANGE or RENAME_NOREPLACE (do not overwrite a surprise path).
    if fn(-100, os.fsencode(left), -100, os.fsencode(right), 2 if exchange else 1):
        error = ctypes.get_errno()
        raise OSError(error, 'Atomic replacement failed: ' + os.strerror(error))


def owned(path: Path):
    if path.is_symlink() or not path.is_file():
        return False
    try:
        text = path.read_text(encoding='utf-8')
        return MARKER in text or ('anticomadreja-manga' in text and 'manga.py' in text)
    except (OSError, UnicodeError):
        return False


def known_app(path: Path):
    if not path.exists():
        return True
    if path.is_symlink() or not path.is_dir():
        return False
    for rel in ('VERSION', 'manga.py'):
        if (path/rel).is_symlink() or not (path/rel).is_file():
            return False
    return bool(re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', (path/'VERSION').read_text().strip()))


def desktop_escape(path: Path):
    return str(path).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')


def check_targets(paths: Paths):
    for key, path in paths.targets.items():
        if key == 'legacy' and not owned(path):
            continue  # Unrelated legacy commands (including symlinks) are untouched.
        no_links(path)
        if path.exists():
            info = path.stat()
            if info.st_uid != os.geteuid() or (key != 'app' and (not path.is_file() or info.st_nlink != 1)):
                raise RuntimeError('Unsafe or foreign-owned installation target: ' + key)
    if not known_app(paths.app):
        raise RuntimeError('Unrecognized application directory; refusing to replace it.')
    if paths.launcher.exists() and not owned(paths.launcher):
        raise RuntimeError('An unrelated manga-cli command already exists; refusing to overwrite it')
    if paths.desktop.exists():
        text = paths.desktop.read_text(encoding='utf-8')
        if 'Name=manga-cli\n' not in text or ('Exec="' + desktop_escape(paths.launcher) + '"\n') not in text:
            raise RuntimeError('An unrelated desktop entry already exists; refusing to overwrite it.')


def check_running(paths: Paths):
    """Best-effort detection for old launchers which cannot hold our install lock."""
    proc = Path('/proc')
    if not proc.is_dir():
        return
    expected = os.fsencode(paths.app/'manga.py')
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid == os.geteuid() and expected in (entry/'cmdline').read_bytes().split(b'\0'):
                raise RuntimeError('Close manga-cli and its reader before changing the installation.')
        except (OSError, PermissionError):
            continue


def inventory(path: Path) -> dict:
    """Inventory bytes, modes and link targets without following links."""
    result = {}
    def visit(item, name):
        info = item.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            result[name] = ['link', os.readlink(item)]
        elif stat.S_ISREG(info.st_mode):
            result[name] = ['file', mode, hashlib.sha256(item.read_bytes()).hexdigest()]
        elif stat.S_ISDIR(info.st_mode):
            result[name] = ['dir', mode]
            for child in sorted(item.iterdir()):
                visit(child, name + '/' + child.name)
        else:
            raise RuntimeError('Special files cannot be included in an installation backup.')
    visit(path, '.')
    return result


def copy_object(source: Path, target: Path):
    expected = inventory(source)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target, follow_symlinks=False)
    if inventory(target) != expected or inventory(source) != expected:
        raise RuntimeError('Backup/staging verification failed: a file changed during copying.')
    sync_tree(target)


def prepare(paths: Paths, key: str, source=None, content=None, mode=0o755):
    target = paths.targets[key]
    target.parent.mkdir(parents=True, exist_ok=True)
    no_links(target)
    holder = Path(tempfile.mkdtemp(prefix='.manga-cli-txn-', dir=target.parent))
    candidate = holder/'new'
    entry = dict(key=key, target=str(target), holder=str(holder), old=identity(target), new=None,
                 desired=source is not None or content is not None)
    try:
        if source is not None:
            copy_object(Path(source), candidate)
        elif content is not None:
            candidate.write_bytes(content)
            candidate.chmod(mode)
            sync_tree(candidate)
        entry['new'] = identity(candidate)
        sync_dir(holder)
        sync_dir(target.parent)
        return entry
    except BaseException:
        if candidate.exists():
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
        holder.rmdir()
        raise


def validate_entry(paths: Paths, entry):
    if entry.get('key') not in KEYS or entry.get('target') != str(paths.targets[entry['key']]):
        raise RuntimeError('Recovery journal contains an unexpected destination.')
    target = Path(entry['target'])
    holder = Path(entry['holder'])
    if holder.parent != target.parent or not re.fullmatch(r'\.manga-cli-txn-[A-Za-z0-9_-]+', holder.name):
        raise RuntimeError('Recovery journal contains an unsafe staging path.')
    no_links(target)
    no_links(holder)
    if holder.exists():
        if not holder.is_dir() or holder.stat().st_uid != os.geteuid():
            raise RuntimeError('Unsafe recovery staging directory.')
        if any(child.name != 'new' for child in holder.iterdir()):
            raise RuntimeError('Unexpected files in recovery staging directory; keeping them intact.')
    for name in ('old', 'new'):
        value = entry.get(name)
        if value is not None and (not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, int) for x in value)):
            raise RuntimeError('Invalid object identity in recovery journal.')
    return target, holder, holder/'new'


def state_of(entry):
    target, candidate = Path(entry['target']), Path(entry['holder'])/'new'
    a, b = identity(target), identity(candidate)
    before = (entry['old'], entry['new'])
    after = (entry['new'], entry['old'])
    if (a, b) == before:
        return 'before'
    if (a, b) == after:
        return 'after'
    raise RuntimeError('Recovery objects changed unexpectedly; all surviving copies have been kept.')


def cleanup(entries, paths):
    for entry in entries:
        target, holder, candidate = validate_entry(paths, entry)
        current = identity(candidate)
        if current is not None:
            if current not in (entry['old'], entry['new']):
                raise RuntimeError('Unknown recovery object; refusing to delete it.')
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
        if holder.exists():
            holder.rmdir()
        sync_dir(target.parent)


def recover(paths: Paths):
    if not paths.journal.exists():
        if paths.journal.is_symlink():
            raise RuntimeError('Unsafe recovery journal link')
        return False
    no_links(paths.journal)
    info = paths.journal.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise RuntimeError('Unsafe recovery journal')
    record = json.loads(paths.journal.read_text(encoding='utf-8'))
    if (record.get('schema') != 1 or record.get('home') != str(paths.home)
            or record.get('data_home') != str(paths.data_home)
            or record.get('state') not in ('pending', 'committed', 'rolled_back')):
        raise RuntimeError('Unrecognized recovery journal or different HOME/XDG_DATA_HOME.')
    entries = record['entries']
    if len({e['key'] for e in entries}) != len(entries) or len(entries) > len(KEYS):
        raise RuntimeError('Duplicate recovery destinations')
    for entry in entries:
        validate_entry(paths, entry)
    if record['state'] == 'pending':
        # Validate the whole set BEFORE touching any destination.
        states = [state_of(entry) for entry in entries]
        for entry, state in reversed(list(zip(entries, states))):
            if state == 'before':
                continue
            target, holder, candidate = validate_entry(paths, entry)
            if entry['old'] is not None and entry['new'] is not None:
                rename_atomic(candidate, target, exchange=True)
            elif entry['old'] is not None:
                rename_atomic(candidate, target)
            else:
                rename_atomic(target, candidate)
            sync_dir(holder)
            sync_dir(target.parent)
        record['state'] = 'rolled_back'
        atomic_json(paths.journal, record)
    else:
        # Cleanup may itself have been interrupted, so candidates can be missing.
        for entry in entries:
            expected = entry['new'] if record['state'] == 'committed' else entry['old']
            if identity(Path(entry['target'])) != expected:
                raise RuntimeError('Installed objects changed before recovery cleanup; refusing automatic removal.')
    cleanup(entries, paths)
    paths.journal.unlink()
    sync_dir(paths.journal.parent)
    return True


def transact(paths: Paths, entries: list[dict]):
    if paths.journal.exists():
        raise RuntimeError('Pending transaction must be recovered before starting another.')
    entries = [entry for entry in entries if entry['old'] is not None or entry['new'] is not None]
    for entry in entries:
        validate_entry(paths, entry)
        if state_of(entry) != 'before':
            raise RuntimeError('A destination changed while preparing the installation.')
    record = dict(schema=1, state='pending', home=str(paths.home), data_home=str(paths.data_home), entries=entries)
    try:
        atomic_json(paths.journal, record)
        checkpoint('journal')
        for entry in entries:
            target, holder, candidate = validate_entry(paths, entry)
            if state_of(entry) != 'before':
                raise RuntimeError('A destination changed during the transaction.')
            if entry['new'] is not None:
                rename_atomic(candidate, target, exchange=entry['old'] is not None)
            else:
                rename_atomic(target, candidate)
            sync_dir(holder)
            sync_dir(target.parent)
            checkpoint(entry['key'])
        record['state'] = 'committed'
        atomic_json(paths.journal, record)
        checkpoint('committed')
    except BaseException:
        try:
            recover(paths)
        except BaseException as error:
            raise RuntimeError('Recovery is still pending. Keep all backup/staging files and rerun install.sh --recover. ' + str(error)) from error
        raise
    recover(paths)  # Committed transaction: remove only its old, verified objects.

BASH_PATH_BEGIN = '# >>> manga-cli PATH >>>'
BASH_PATH_END = '# <<< manga-cli PATH <<<'
BASH_PATH_BLOCK = (
    BASH_PATH_BEGIN + "\n"
    'if [ -x "$HOME/.local/bin/manga-cli" ]; then\n'
    '    case ":$PATH:" in\n'
    '        *":$HOME/.local/bin:"*) ;;\n'
    '        *) export PATH="$HOME/.local/bin:$PATH" ;;\n'
    '    esac\n'
    'fi\n'
    + BASH_PATH_END + "\n"
)


def _safe_user_text_file(path: Path):
    no_links(path)
    if not path.exists():
        return None
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise RuntimeError('Unsafe or foreign-owned shell startup file: ' + str(path))
    return info


def _atomic_user_text(path: Path, text: str, mode: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    no_links(path)
    fd, name = tempfile.mkstemp(prefix='.manga-cli-shell-', dir=path.parent)
    temp = Path(name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        no_links(path)
        os.replace(temp, path)
        sync_dir(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def ensure_bash_path(paths: Paths):
    """Make the installed command visible in new default-Bash terminals.

    Debian/Xfce terminals start interactive non-login Bash, which may not read
    ~/.profile. Add one marked, app-specific block to ~/.bashrc only when Bash is
    the user's login shell. Existing unrelated shell configuration is preserved.
    """
    shell = os.environ.get('SHELL', '')
    if not shell:
        try:
            import pwd
            shell = pwd.getpwuid(os.geteuid()).pw_shell
        except (KeyError, ImportError):
            shell = ''
    if Path(shell).name != 'bash':
        return 'not-bash'
    path = paths.home/'.bashrc'
    try:
        info = _safe_user_text_file(path)
        old = path.read_text(encoding='utf-8') if info else ''
        if BASH_PATH_BEGIN in old or BASH_PATH_END in old:
            return 'present' if BASH_PATH_BLOCK in old else 'unsafe'
        sep = '' if not old or old.endswith('\n') else '\n'
        prefix = '' if not old else '\n'
        _atomic_user_text(path, old + sep + prefix + BASH_PATH_BLOCK,
                          stat.S_IMODE(info.st_mode) if info else 0o644)
        return 'added'
    except (OSError, UnicodeError, RuntimeError):
        return 'unsafe'


def remove_bash_path(paths: Paths):
    """Remove only the exact block previously written by ensure_bash_path()."""
    path = paths.home/'.bashrc'
    try:
        info = _safe_user_text_file(path)
        if not info:
            return False
        old = path.read_text(encoding='utf-8')
        if BASH_PATH_BLOCK not in old:
            return False
        new = old.replace(BASH_PATH_BLOCK, '', 1)
        # Undo the one blank separator inserted before the managed block.
        if new.endswith('\n\n'):
            new = new[:-1]
        _atomic_user_text(path, new, stat.S_IMODE(info.st_mode))
        return True
    except (OSError, UnicodeError, RuntimeError):
        return False

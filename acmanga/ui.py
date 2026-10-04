"""Retro terminal UI with live accent colors and cell-safe text frames."""
import os
import select
import shutil
import sys

from .i18n import tr
from .theme import PALETTES, normalize_accent
from .terminal_text import cell_width, clean_text, compact, pad

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
WHITE = "\033[37m"
REV = "\033[7m"


def _fg256(index):
    return "\033[38;5;{}m".format(int(index))


def apply_theme(name):
    """Update role colors immediately, including legacy UI role aliases."""
    global THEME_NAME, DARK_WINE, MAROON, BURGUNDY, BLOOD, CRIMSON
    global ROSE, SCARLET, SOFT_RED, PALE_ROSE, CYAN, MAGENTA
    global ACCENT, ACCENT_SOFT, TEXT_WARM
    THEME_NAME = normalize_accent(name)
    palette = PALETTES[THEME_NAME]
    DARK_WINE = MAROON = _fg256(palette.border)
    BURGUNDY = BLOOD = _fg256(palette.low)
    CRIMSON = _fg256(palette.heading)
    ROSE = SCARLET = CYAN = MAGENTA = ACCENT = _fg256(palette.accent)
    SOFT_RED = ACCENT_SOFT = _fg256(palette.soft)
    PALE_ROSE = TEXT_WARM = _fg256(palette.text)
    return THEME_NAME


apply_theme('crimson')
SAFE_MARGIN = 6
MAX_CANVAS_WIDTH = 120


def color_enabled():
    return sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb" and os.environ.get("NO_COLOR") is None


def paint(text, *codes):
    if not color_enabled() or not codes:
        return str(text)
    return "".join(codes) + str(text) + RESET


def clear():
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def term_size():
    return shutil.get_terminal_size((80, 28))


def width():
    cols = max(4, term_size().columns)
    return min(max(cols - SAFE_MARGIN, min(cols, 24)), MAX_CANVAS_WIDTH)


def margin():
    return max(0, (term_size().columns - width()) // 2)


def emit(text=""):
    print(" " * margin() + text)


def list_rows(extra=0):
    return max(3, min(30, term_size().lines - 13 - extra))


def rule(char="─", code=None):
    emit(paint(char * width(), MAROON if code is None else code))


def brand(version, subtitle=None):
    frame_top()
    frame_line(tr('brand.name'), 'v{}'.format(version), (BOLD, ACCENT), (BOLD, CRIMSON))
    frame_line(subtitle if subtitle is not None else tr('brand.subtitle'), '', (DIM,), ())
    frame_bottom()


def banner(title=None, subtitle=None):
    w = width()
    title = compact(str(title or tr('brand.name')).upper(), max(0, w - 8))
    label = ' {} '.format(title)
    emit(paint('╭─' + label + '─' * max(0, w - 3 - cell_width(label)) + '╮', BLOOD, BOLD))
    if subtitle:
        frame_line(subtitle, '', (DIM,), ())
    frame_bottom()


def section(title, right=None):
    w = width()
    right_text = compact(right, max(0, w // 2 - 2)) if right else ''
    right_text = ' {} '.format(right_text) if right_text else ''
    left = compact(str(title).upper(), max(0, w - cell_width(right_text) - 3))
    left = ' {} '.format(left)
    fill = max(0, w - cell_width(left) - cell_width(right_text))
    emit(paint(left, BOLD, CRIMSON) + paint('─' * fill, MAROON) + paint(right_text, DIM))


def frame_top():
    emit(paint('╭' + '─' * (width() - 2) + '╮', MAROON))


def frame_rule():
    emit(paint('├' + '─' * (width() - 2) + '┤', MAROON, DIM))


def frame_bottom():
    emit(paint('╰' + '─' * (width() - 2) + '╯', MAROON))


def frame_line(left='', right='', left_codes=(), right_codes=()):
    inner = width() - 4
    left, right = clean_text(left), clean_text(right)
    if right and left and inner >= 3:
        # Reserve the label before giving a long field its remaining columns.
        label_reserve = min(cell_width(left), max(1, inner // 2))
        right = compact(right, max(0, inner - label_reserve - 1))
        left = compact(left, max(0, inner - cell_width(right) - 1))
    elif right:
        right = compact(right, inner)
        left = ''
    else:
        left = compact(left, inner)
    gap = max(0, inner - cell_width(left) - cell_width(right))
    emit(paint('│ ', MAROON) + paint(left, *left_codes) + ' ' * gap +
         paint(right, *right_codes) + paint(' │', MAROON))


def frame_blank():
    frame_line('')


def status(text, kind='info'):
    code = {'ok': GREEN, 'warn': YELLOW, 'error': RED, 'info': ACCENT}.get(kind, ACCENT)
    symbol = {'ok': '●', 'warn': '!', 'error': '×', 'info': '·'}.get(kind, '·')
    emit('{} {}'.format(paint(symbol, code, BOLD), paint(compact(text, width() - 2), code)))


def key_hint(items):
    chunks, used = [], 0
    for key, desc in items:
        key = compact(key, max(1, width() // 2))
        desc = compact(desc, max(0, width() - cell_width(key) - 1))
        length = cell_width(key) + 1 + cell_width(desc)
        if chunks and used + 3 + length > width():
            emit('   '.join(chunks))
            chunks, used = [], 0
        if chunks:
            used += 3
        chunks.append('{} {}'.format(paint(key, BOLD, CRIMSON), paint(desc, DIM)))
        used += length
    if chunks:
        emit('   '.join(chunks))


def badge(text, code=None):
    return paint('[{}]'.format(clean_text(text)), BOLD, BLOOD if code is None else code)


def progress_bar(current, total, length=12):
    try:
        current = max(0, int(current or 0))
        total = max(0, int(total or 0))
    except (TypeError, ValueError):
        return ''
    if total <= 0:
        return ''
    current = min(current, total)
    filled = max(0, min(length, int(round((float(current) / float(total)) * length))))
    return paint('━' * filled, CRIMSON) + paint('─' * (length - filled), DIM)


def source_badge(manga):
    sources = {v.get('source') for v in manga.get('variants') or []}
    return '+'.join(label for key, label in (('mangakatana', 'MK'), ('mangapill', 'MP'))
                    if key in sources) or '?'


def chapter_source_badge(chapter):
    return {'mangakatana': 'MK', 'mangapill': 'MP'}.get(chapter.get('source'), '?')


def read_key(timeout=None):
    """Read one key without Enter on a Linux TTY; use input() otherwise."""
    if not sys.stdin.isatty():
        try:
            value = input().strip()
        except (EOFError, KeyboardInterrupt):
            return "esc"
        return value[:1].lower() if value else "enter"

    try:
        import termios
        import tty
    except ImportError:
        try:
            value = input().strip()
        except (EOFError, KeyboardInterrupt):
            return "esc"
        return value[:1].lower() if value else "enter"

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        if timeout is not None and not select.select([fd], [], [], timeout)[0]:
            return ""
        ch = os.read(fd, 1)
        if ch in (b"\r", b"\n"):
            return "enter"
        if ch == b"\x03":
            return "esc"
        if ch == b"\x1b":
            seq = bytearray(ch)
            while len(seq) < 8:
                ready, _w, _x = select.select([fd], [], [], 0.04)
                if not ready:
                    break
                seq.extend(os.read(fd, 1))
                if seq[-1:] in (b"~", b"A", b"B", b"C", b"D", b"H", b"F"):
                    break
            mapping = {
                b"\x1b[A": "up",
                b"\x1b[B": "down",
                b"\x1b[C": "right",
                b"\x1b[D": "left",
                b"\x1b[5~": "pgup",
                b"\x1b[6~": "pgdn",
                b"\x1b[H": "home",
                b"\x1b[F": "end",
                b"\x1b[1~": "home",
                b"\x1b[4~": "end",
            }
            return mapping.get(bytes(seq), "esc")
        try:
            return ch.decode("utf-8", "ignore").lower() or ""
        except Exception:
            return ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def prompt_line(label):
    """Minimal text input. Escape cancels without requiring Enter."""
    prefix = " " * margin()
    if not sys.stdin.isatty():
        try:
            return input(prefix + label).strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    try:
        import termios
        import tty
    except ImportError:
        try:
            return input(prefix + label).strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    sys.stdout.write(prefix + label)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    data = bytearray()
    try:
        tty.setraw(fd)
        while True:
            ch = os.read(fd, 1)
            if not ch:
                break
            if ch in (b"\r", b"\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                break
            if ch in (b"\x1b", b"\x03"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                return ""
            if ch in (b"\x7f", b"\x08"):
                if data:
                    while data and (data[-1] & 0xC0) == 0x80:
                        data.pop()
                    if data:
                        data.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if ch[0] >= 32 or ch[0] >= 128:
                data.extend(ch)
                try:
                    sys.stdout.buffer.write(ch)
                    sys.stdout.buffer.flush()
                except Exception:
                    pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return data.decode("utf-8", "ignore").strip()

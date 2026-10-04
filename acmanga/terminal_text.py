"""Single-line, cell-aware terminal text without third-party dependencies.

Wide/fullwidth characters occupy two cells; combining marks occupy zero.
Ambiguous characters use the one-cell convention of Western UTF-8 terminals.
Control sequences and bidi format controls from provider text are not rendered.
Emoji presentation differs between terminals; conservative cell budgets may
truncate them early rather than allow them to cross a frame border.
"""
import re
import unicodedata

# Strip OSC/DCS and CSI sequences before measuring or rendering provider text.
_ESCAPES = re.compile(
    r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)'
    r'|\x1b[P^_].*?(?:\x1b\\|$)'
    r'|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]'
    r'|\x1b[@-_]', re.DOTALL)


def clean_text(value):
    value = _ESCAPES.sub('', str(value if value is not None else ''))
    # Keep word boundaries from tabs/newlines; strip invisible format controls.
    value = ' '.join(value.split())
    return ''.join(ch for ch in value if unicodedata.category(ch) not in ('Cc', 'Cf', 'Cs'))


def _char_width(ch):
    if unicodedata.category(ch) in ('Mn', 'Me'):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1


def _clusters(text):
    current, cells = '', 0
    for ch in text:
        size = _char_width(ch)
        if not size:
            if current:
                current += ch
                if ch in ("\ufe0f", "\u20e3"):
                    cells = max(cells, 2)
            continue
        if current:
            yield current, cells
        current, cells = ch, size
    if current:
        yield current, cells


def cell_width(value):
    text = _ESCAPES.sub('', str(value if value is not None else ''))
    text = ''.join(ch for ch in text if unicodedata.category(ch) not in ('Cc', 'Cf', 'Cs'))
    return sum(size for _, size in _clusters(text))


def _take(text, budget):
    out, used = [], 0
    for cluster, size in _clusters(text):
        if used + size > budget:
            break
        out.append(cluster)
        used += size
    return ''.join(out)


def compact(value, limit):
    """Truncate a single line with ASCII '...', within a visual cell budget."""
    limit = max(0, int(limit))
    text = clean_text(value)
    if cell_width(text) <= limit:
        # Also discard unattached leading combining marks.
        return ''.join(cluster for cluster, _ in _clusters(text))
    if limit <= 3:
        return '.' * limit
    return _take(text, limit - 3) + '...'


def pad(value, columns):
    columns = max(0, int(columns))
    text = compact(value, columns)
    return text + ' ' * (columns - cell_width(text))

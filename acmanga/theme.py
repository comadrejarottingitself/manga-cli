"""Terminal accent palettes. Semantic success/warning/error colors live in ui."""
from collections import namedtuple
from types import MappingProxyType

Palette = namedtuple('Palette', 'border low heading accent soft text')
PALETTES = MappingProxyType({
    'crimson': Palette(88, 124, 160, 203, 210, 217),
    'red': Palette(88, 124, 196, 196, 203, 210),
    'orange': Palette(94, 130, 208, 214, 215, 223),
    'amber': Palette(94, 136, 214, 220, 222, 229),
    'green': Palette(22, 28, 34, 40, 114, 157),
    'lime': Palette(58, 64, 112, 154, 191, 193),
    'cyan': Palette(23, 30, 44, 51, 87, 159),
    'blue': Palette(17, 25, 33, 75, 111, 153),
    'purple': Palette(54, 91, 135, 141, 177, 183),
    'magenta': Palette(89, 125, 164, 201, 213, 219),
})
ACCENT_COLORS = tuple(PALETTES)
DEFAULT_ACCENT = 'crimson'


def normalize_accent(value):
    if isinstance(value, str) and value in PALETTES:
        return value
    return DEFAULT_ACCENT

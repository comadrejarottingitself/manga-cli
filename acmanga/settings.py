import json
from pathlib import Path

from .util import atomic_json_write
from .theme import normalize_accent

SCHEMA = 2
SOURCE_MODES = ("auto", "mangakatana", "mangapill")
SOURCE_ORDERS = (
    ("mangakatana", "mangapill"),
    ("mangapill", "mangakatana"),
)
SAVED_SORTS = ("activity", "title", "saved")


def default_settings():
    return {
        "schema": SCHEMA,
        "accent_color": "crimson",
        "source_mode": "auto",
        "source_priority": ["mangakatana", "mangapill"],
        "saved_sort": "activity",
        "reader_fit": "page",
        "auto_page_turn": True,
        "remember_reader_mode": True,
        "show_page_indicator": True,
        "save_reader_position": True,
        "prefetch_pages": 3,
        "prefetch_next_chapter": True,
        "cache_mib": 512,
        "scroll_step": 0.10,
        "mobile_double_tap_zoom": 2.0,
    }


def _normalized(data):
    base = default_settings()
    if not isinstance(data, dict):
        return base

    base["accent_color"] = normalize_accent(data.get("accent_color"))

    mode = data.get("source_mode")
    if mode in SOURCE_MODES:
        base["source_mode"] = mode

    priority = data.get("source_priority")
    if isinstance(priority, list) and tuple(priority) in SOURCE_ORDERS:
        base["source_priority"] = list(priority)

    saved_sort = data.get("saved_sort")
    if saved_sort in SAVED_SORTS:
        base["saved_sort"] = saved_sort

    if data.get("reader_fit") in ("width", "page"):
        base["reader_fit"] = data["reader_fit"]
    for key in ("prefetch_next_chapter", "auto_page_turn", "remember_reader_mode",
                "show_page_indicator", "save_reader_position"):
        if isinstance(data.get(key), bool):
            base[key] = data[key]
    for key, lo, hi, kind in [("prefetch_pages",0,10,int),("cache_mib",64,4096,int),("scroll_step",0.02,0.5,float),("mobile_double_tap_zoom",1.25,3.0,float)]:
        try:
            import math
            value = kind(data.get(key, base[key]))
            if math.isfinite(value):
                base[key] = max(lo, min(hi, value))
        except (ValueError, TypeError, OverflowError):
            pass
    return base


def load_settings(path):
    path = Path(path)
    if not path.exists():
        return default_settings()
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return default_settings()
    return _normalized(data)


def save_settings(path, settings):
    clean = _normalized(settings)
    clean["schema"] = SCHEMA
    atomic_json_write(path, clean)
    settings.clear()
    settings.update(clean)
    return settings

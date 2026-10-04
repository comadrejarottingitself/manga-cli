"""Central English catalogue. Message keys never double as state/protocol keys."""
import json
from pathlib import Path
from types import MappingProxyType

with Path(__file__).with_name("locales").joinpath("en.json").open(encoding="utf-8") as handle:
    TEXT = MappingProxyType(json.load(handle))


def tr(key, *args, **kwargs):
    """Return a message; fail loudly for unknown keys during development/tests."""
    value = TEXT[key]
    return value.format(*args, **kwargs) if args or kwargs else value

"""Persist Settings to a JSON file next to main.py.

Saves every simple-typed field (bool/int/float/str). Container fields like
tuples are skipped — those still live in settings.py.
"""
import json
import logging
from pathlib import Path

log = logging.getLogger("fh5ds")

PATH = Path(__file__).resolve().parent.parent / "user_preferences.json"

_SIMPLE = (bool, int, float, str)


def _keys(s) -> list[str]:
    return [k for k, v in vars(s).items() if isinstance(v, _SIMPLE)]


def load(s) -> None:
    if not PATH.exists():
        return
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Could not load preferences (%s): %s", PATH.name, e)
        return
    keys = set(_keys(s))
    for k, v in data.items():
        if k not in keys:
            continue
        current = getattr(s, k)
        try:
            if isinstance(current, bool):
                setattr(s, k, bool(v))
            elif isinstance(current, int):
                setattr(s, k, int(v))
            elif isinstance(current, float):
                setattr(s, k, float(v))
            else:
                setattr(s, k, v)
        except (TypeError, ValueError):
            log.warning("Bad preference value for %s: %r", k, v)


def save(s) -> None:
    data = {k: getattr(s, k) for k in _keys(s)}
    try:
        PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Could not save preferences (%s): %s", PATH.name, e)

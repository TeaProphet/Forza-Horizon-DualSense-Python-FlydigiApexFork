"""Persist Settings to a JSON file next to main.py.

Saves simple-typed fields (bool/int/float/str). On version change the file
is wiped so defaults from settings.py apply.
"""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("fh5ds")

PATH = Path(__file__).resolve().parent.parent / "user_preferences.json"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

_SIMPLE = (bool, int, float, str)


def _version() -> str:
    m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"))
    return m.group(1) if m else ""


def _fields(s) -> dict:
    return {k: v for k, v in vars(s).items() if isinstance(v, _SIMPLE)}


def load(s) -> None:
    if not PATH.exists():
        return
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Could not load preferences: %s", e)
        return
    if data.get("version") != _version():
        log.info("Resetting preferences: version changed.")
        PATH.unlink(missing_ok=True)
        return
    for k, current in _fields(s).items():
        if k in data:
            try:
                setattr(s, k, type(current)(data[k]))
            except (TypeError, ValueError):
                pass


def save(s) -> None:
    data = _fields(s) | {"version": _version()}
    try:
        PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Could not save preferences: %s", e)

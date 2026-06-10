"""Named Settings snapshots stored inside user_preferences.json."""
import base64
import json
import logging
import zlib

from . import preferences

log = logging.getLogger("fhds")

SHARE_PREFIX = "FHDS:"
_DEFAULT = preferences.DEFAULT_PROFILE_NAME


def load_profiles() -> dict:
    """Snapshot of {'active': str, 'profiles': dict} from disk."""
    raw = preferences._read()
    return {
        "active": raw.get("active_profile", "") or "",
        "profiles": raw.get("profiles", {}) or {},
    }


def list_profile_names(store: dict) -> list:
    """All profile names with Default pinned to the top."""
    names = list(store.get("profiles", {}).keys())
    rest = sorted((n for n in names if n != _DEFAULT), key=str.lower)
    return ([_DEFAULT] + rest) if _DEFAULT in names else rest


def _unique(name: str, taken: dict) -> str:
    """Return `name` or `name1`, `name2`, ... if it collides."""
    if name not in taken:
        return name
    i = 1
    while f"{name}{i}" in taken:
        i += 1
    return f"{name}{i}"


def _write_store(profs: dict, active: str) -> None:
    raw = preferences._read()
    raw["profiles"] = profs
    raw["active_profile"] = active
    preferences._write(raw)


def _defaults() -> dict:
    from .settings import Settings
    return preferences._profile_fields(Settings())


def save_profile(name: str, s) -> str:
    """Save current settings as a new profile. Auto-suffixes on collision.
    Returns the final stored name, or "" if `name` was empty."""
    name = name.strip()
    if not name:
        return ""
    store = load_profiles()
    final = _unique(name, store["profiles"])
    store["profiles"][final] = preferences._profile_fields(s)
    _write_store(store["profiles"], final)
    return final


def apply_profile(name: str, s) -> bool:
    store = load_profiles()
    snap = store["profiles"].get(name)
    if snap is None:
        return False
    preferences._apply_snap(s, snap, preferences._profile_fields(s))
    _write_store(store["profiles"], name)
    return True


def delete_profile(name: str) -> bool:
    store = load_profiles()
    profs = store["profiles"]
    if name not in profs or name == _DEFAULT:
        return False
    del profs[name]
    active = store["active"]
    if active == name:
        # Prefer Default so the canonical profile stays selected.
        active = _DEFAULT if _DEFAULT in profs else next(
            iter(sorted(profs.keys(), key=str.lower)), "")
    _write_store(profs, active)
    return True


def rename_profile(old: str, new: str) -> str:
    """Rename `old` to `new`, auto-suffixing on collision. Returns "" if
    rejected (Default locked, old missing, new empty)."""
    new = new.strip()
    if not new or old == new or old == _DEFAULT:
        return ""
    store = load_profiles()
    profs = store["profiles"]
    if old not in profs:
        return ""
    final = _unique(new, {k: v for k, v in profs.items() if k != old})
    # Preserve insertion order so the list doesn't reshuffle.
    profs_new = {(final if k == old else k): v for k, v in profs.items()}
    active = final if store["active"] == old else store["active"]
    _write_store(profs_new, active)
    return final


# MARK: share codes --------------------------------------------------------

def export_profile(name: str) -> str:
    """Encode profile `name` as a short FHDS: code. Empty if missing.
    Only fields that differ from current built-in defaults are encoded."""
    store = load_profiles()
    snap = store["profiles"].get(name)
    if snap is None:
        return ""
    defaults = _defaults()
    diff = {k: v for k, v in snap.items() if defaults.get(k) != v}
    payload = json.dumps([name, diff], separators=(",", ":")).encode("utf-8")
    blob = zlib.compress(payload, level=9)
    return SHARE_PREFIX + base64.urlsafe_b64encode(blob).rstrip(b"=").decode("ascii")


def import_profile(code: str) -> str:
    """Decode an FHDS: code into a new profile (auto-suffixed). Returns ""
    on failure. Unknown keys are dropped; missing keys fall back to current
    defaults so codes stay compatible across versions."""
    code = (code or "").strip()
    if not code.startswith(SHARE_PREFIX):
        return ""
    body = code[len(SHARE_PREFIX):]
    pad = "=" * (-len(body) % 4)
    try:
        blob = base64.urlsafe_b64decode(body + pad)
        payload = json.loads(zlib.decompress(blob).decode("utf-8"))
    except (ValueError, OSError, zlib.error, json.JSONDecodeError):
        return ""
    if not (isinstance(payload, list) and len(payload) == 2
            and isinstance(payload[1], dict)):
        return ""
    name = str(payload[0]).strip() or "Imported"
    defaults = _defaults()
    cleaned = {k: v for k, v in payload[1].items() if k in defaults}
    store = load_profiles()
    final = _unique(name, store["profiles"])
    store["profiles"][final] = {**defaults, **cleaned}
    _write_store(store["profiles"], store["active"])
    return final

"""Named Settings snapshots managed inside user_preferences.json.

Profiles share the same file as preferences. Operations always re-read from
disk and write back, so the in-memory store stays in sync without callers
holding references to a particular dict.
"""
import logging

from modules import preferences

log = logging.getLogger("fhds")


def load_store() -> dict:
    """Snapshot of the profiles block on disk."""
    raw = preferences._read()
    return {
        "active": raw.get("active_profile", "") or "",
        "profiles": raw.get("profiles", {}) or {},
    }


def list_names(store: dict) -> list:
    """All profile names, with Default pinned to the top."""
    names = list(store.get("profiles", {}).keys())
    rest = sorted((n for n in names if n != preferences.DEFAULT_PROFILE_NAME),
                  key=str.lower)
    if preferences.DEFAULT_PROFILE_NAME in names:
        return [preferences.DEFAULT_PROFILE_NAME] + rest
    return rest


def _unique_name(name: str, taken: dict) -> str:
    """Return `name`, or `name1`, `name2`, ... if it collides with `taken`."""
    if name not in taken:
        return name
    i = 1
    while f"{name}{i}" in taken:
        i += 1
    return f"{name}{i}"


def _persist(profs: dict, active: str) -> None:
    raw = preferences._read()
    raw["profiles"] = profs
    raw["active_profile"] = active
    preferences._write(raw)


def save_as(name: str, s) -> str:
    """Save current settings as a new profile. If `name` is already taken,
    an incremental suffix is appended. Returns the final stored name (or ""
    if the input was empty)."""
    name = name.strip()
    if not name:
        return ""
    store = load_store()
    final = _unique_name(name, store["profiles"])
    store["profiles"][final] = preferences._profile_fields(s)
    store["active"] = final
    _persist(store["profiles"], store["active"])
    return final


def apply(name: str, s) -> bool:
    store = load_store()
    snap = store["profiles"].get(name)
    if snap is None:
        return False
    preferences._apply_snap(s, snap, preferences._profile_fields(s))
    _persist(store["profiles"], name)
    return True


def delete(name: str) -> bool:
    store = load_store()
    profs = store["profiles"]
    if name not in profs or name == preferences.DEFAULT_PROFILE_NAME:
        return False
    del profs[name]
    active = store["active"]
    if active == name:
        # Prefer Default as the new active so it stays as the canonical pick.
        active = (preferences.DEFAULT_PROFILE_NAME
                  if preferences.DEFAULT_PROFILE_NAME in profs
                  else next(iter(sorted(profs.keys(), key=str.lower)), ""))
    _persist(profs, active)
    return True


def rename(old: str, new: str) -> str:
    """Rename `old` to `new`. If `new` collides, an incremental suffix is
    appended. Returns the final stored name, or "" if the rename was
    rejected (Default is locked, old missing, or new empty)."""
    new = new.strip()
    if not new or old == new or old == preferences.DEFAULT_PROFILE_NAME:
        return ""
    store = load_store()
    profs = store["profiles"]
    if old not in profs:
        return ""
    final = _unique_name(new, {k: v for k, v in profs.items() if k != old})
    # Preserve insertion order so the list doesn't reshuffle.
    profs_new = {(final if k == old else k): v for k, v in profs.items()}
    active = final if store["active"] == old else store["active"]
    _persist(profs_new, active)
    return final


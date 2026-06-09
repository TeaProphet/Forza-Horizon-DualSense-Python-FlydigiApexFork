"""Updates tab: toggle ZUV's launch-time update prompt.

The ZUV loader runs *before* this app starts, so toggling here only affects
the next launch. The mechanism is a sentinel file (.zuv-update-disabled) the
loader checks in its cache_root; when present, the update check is skipped.
ZUV exports cache_root via the ZUV_CACHE_ROOT env var.
"""
import logging
import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Label, Switch

from modules import preferences

log = logging.getLogger("fhds")

SENTINEL = ".zuv-update-disabled"


def _sentinel_path() -> Path | None:
    root = os.environ.get("ZUV_CACHE_ROOT")
    return Path(root) / SENTINEL if root else None


def _apply_sentinel(enabled: bool) -> None:
    """Reconcile the on-disk sentinel with the desired setting.
    enabled=True  -> updates wanted -> remove sentinel.
    enabled=False -> updates off    -> create sentinel.
    No-op when running outside a ZUV bundle (no ZUV_CACHE_ROOT)."""
    path = _sentinel_path()
    if path is None:
        return
    try:
        if enabled:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
    except OSError as e:
        log.warning("Could not update %s: %s", SENTINEL, e)


class UpdatesTab(VerticalScroll):
    DEFAULT_CSS = """
    UpdatesTab { width: 1fr; height: 1fr; padding: 1 2; }
    UpdatesTab Label.section { text-style: bold; color: $accent; padding: 1 0 0 1; }
    UpdatesTab .row { height: 3; width: 1fr; align-vertical: middle; padding: 0 1; }
    UpdatesTab .row Label { width: 1fr; height: 3; content-align: left middle; }
    UpdatesTab .row Switch { margin-right: 2; }
    UpdatesTab Label.hint {
        width: 1fr; height: auto;
        color: $text-muted; padding: 0 1 1 1;
    }
    UpdatesTab Label.error {
        width: 1fr; height: auto;
        color: $error; padding: 1; text-style: bold;
    }
    """

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        yield Label("Updates", classes="section")
        if _sentinel_path() is None:
            yield Label(
                "ZUV not found: this build is not running inside a ZUV bundle "
                "(ZUV_CACHE_ROOT env var is missing), so the update toggle has "
                "nothing to control. Run the bundled .zuv.py to manage updates.",
                classes="error",
            )
            return
        with Horizontal(classes="row"):
            yield Switch(value=self.settings.check_for_updates, id="check_for_updates")
            yield Label("Check for updates at launch")
        yield Label(
            "When off, ZUV will not prompt for updates on startup. "
            "Toggle on and restart the app to check for a new release.",
            classes="hint",
        )

    def on_mount(self) -> None:
        if _sentinel_path() is None:
            return
        # Reconcile sentinel with stored setting in case cache was wiped or
        # the prefs file was edited externally.
        _apply_sentinel(self.settings.check_for_updates)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id != "check_for_updates":
            return
        if self.settings.check_for_updates == event.value:
            return
        self.settings.check_for_updates = event.value
        preferences.save(self.settings)
        _apply_sentinel(event.value)
        log.info("check_for_updates = %s", event.value)

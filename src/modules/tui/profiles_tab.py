"""Profiles tab: manage named Settings snapshots."""
import logging

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from modules import preferences, profiles

log = logging.getLogger("fhds")


class ProfilesTab(Vertical):
    DEFAULT_CSS = """
    ProfilesTab { width: 1fr; height: 1fr; padding: 1 2; }
    ProfilesTab .header { width: 1fr; height: auto; }
    ProfilesTab .header Label { width: auto; padding: 0 1 0 0; text-style: bold; color: $accent; }
    ProfilesTab #profile-active {
        width: 1fr; height: 1; padding: 0 1;
        color: $text-muted; content-align: right middle;
    }
    ProfilesTab #profile-list {
        width: 1fr; height: 1fr; min-height: 5;
        border: round $accent 40%; margin: 0 0 1 0;
    }
    ProfilesTab #profile-list > ListItem { padding: 0 1; }
    ProfilesTab #profile-list > ListItem.--highlight { background: $accent 30%; }
    ProfilesTab .toolbar { width: 1fr; height: auto; margin: 0 0 1 0; }
    ProfilesTab .toolbar Button { width: 1fr; margin: 0 1 0 0; }
    ProfilesTab .toolbar Button:last-of-type { margin: 0; }
    ProfilesTab .save-row { width: 1fr; height: auto; }
    ProfilesTab .save-row Input { width: 1fr; margin: 0 1 0 0; }
    ProfilesTab .save-row Button { width: 12; }
    ProfilesTab #profile-path {
        width: 1fr; height: auto; padding: 1 0 0 0;
        color: $text-muted; text-style: italic;
    }
    App.-narrow ProfilesTab .toolbar { layout: vertical; }
    App.-narrow ProfilesTab .toolbar Button { width: 1fr; margin: 0 0 1 0; }
    App.-narrow ProfilesTab .save-row { layout: vertical; }
    App.-narrow ProfilesTab .save-row Input { width: 1fr; margin: 0 0 1 0; }
    App.-narrow ProfilesTab .save-row Button { width: 1fr; }
    """

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        with Horizontal(classes="header"):
            yield Label("Profiles")
            yield Static(self._active_text(), id="profile-active")
        yield ListView(id="profile-list")
        with Horizontal(classes="toolbar"):
            yield Button("Load", id="profile-load", variant="primary")
            yield Button("Rename", id="profile-rename")
            yield Button("Delete", id="profile-delete", variant="error")
        with Horizontal(classes="save-row"):
            yield Input(placeholder="New profile name", id="profile-name")
            yield Button("Save", id="profile-save", variant="success")
        yield Static(f"File: {preferences.PATH}", id="profile-path")

    def on_mount(self):
        self.refresh_list()

    def _active_text(self) -> str:
        store = profiles.load_store()
        active = store.get("active") or "(none)"
        return f"Active: [b]{active}[/b]"

    def refresh_list(self):
        store = profiles.load_store()
        lv = self.query_one("#profile-list", ListView)
        active = store.get("active", "")
        lv.clear()
        for name in profiles.list_names(store):
            label = f"{name}  [dim](active)[/]" if name == active else name
            lv.append(ListItem(Static(label, markup=True), name=name))
        self.query_one("#profile-active", Static).update(self._active_text())
        if hasattr(self.app, "refresh_profile"):
            self.app.refresh_profile()

    def _selected_name(self) -> str:
        lv = self.query_one("#profile-list", ListView)
        item = lv.highlighted_child
        return item.name if item and item.name else ""

    def _name_input(self) -> Input:
        return self.query_one("#profile-name", Input)

    def _save_from_input(self):
        widget = self._name_input()
        name = widget.value.strip()
        if not name:
            log.warning("Profile name is empty.")
            return
        final = profiles.save_as(name, self.settings)
        widget.value = ""
        self.refresh_list()
        if final and final != name:
            log.info("Saved profile: %s (renamed from %s, name taken)", final, name)
        else:
            log.info("Saved profile: %s", final)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "profile-name":
            self._save_from_input()

    def on_button_pressed(self, event: Button.Pressed):
        bid = event.button.id
        if bid == "profile-save":
            self._save_from_input()
        elif bid == "profile-load":
            name = self._selected_name()
            if not name:
                log.warning("No profile selected.")
                return
            if profiles.apply(name, self.settings):
                self.app.refresh_setting_widgets()
                self.refresh_list()
                log.info("Loaded profile: %s", name)
        elif bid == "profile-delete":
            name = self._selected_name()
            if not name:
                log.warning("No profile selected.")
                return
            if name == preferences.DEFAULT_PROFILE_NAME:
                log.warning("Default profile cannot be deleted.")
                return
            if profiles.delete(name):
                self.refresh_list()
                log.info("Deleted profile: %s", name)
        elif bid == "profile-rename":
            old = self._selected_name()
            if not old:
                log.warning("No profile selected.")
                return
            if old == preferences.DEFAULT_PROFILE_NAME:
                log.warning("Default profile cannot be renamed.")
                return
            new = self._name_input().value.strip()
            if not new:
                log.warning("Type the new name in the name field first.")
                return
            final = profiles.rename(old, new)
            if not final:
                log.warning("Rename failed.")
                return
            self._name_input().value = ""
            self.refresh_list()
            if final != new:
                log.info("Renamed profile: %s -> %s (name taken)", old, final)
            else:
                log.info("Renamed profile: %s -> %s", old, final)

"""LANG tab: pick the UI language. Languages are auto-discovered from the
`lang` package (one module per language); dropping a new `<code>.py` there makes
it show up here automatically. Restart-to-apply: the choice is saved and the UI
re-renders in the new language on next launch."""
import logging

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Label, ListItem, ListView, Static

from lang import available, set_language, t
from modules.config import preferences

log = logging.getLogger("fhds")


class LangTab(VerticalScroll):
    DEFAULT_CSS = """
    LangTab { width: 1fr; height: 1fr; padding: 1 2; }
    LangTab Label.section { text-style: bold; color: $accent; padding: 1 0 0 1; }
    LangTab #lang-list {
        width: 1fr; height: auto; min-height: 5; max-height: 20;
        border: round $accent 40%; margin: 1 0;
    }
    LangTab #lang-list > ListItem { padding: 0 1; }
    LangTab #lang-list > ListItem.--highlight { background: $accent 30%; }
    LangTab Label.hint { width: 1fr; height: auto; color: $text-muted; padding: 0 1; }
    LangTab #lang-note { width: 1fr; height: auto; padding: 1 1 0 1; color: $warning; }
    """

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        yield Label(t("Language"), classes="section")
        yield ListView(id="lang-list")
        yield Label(t("Pick a language, then restart the app to apply it."), classes="hint")
        yield Static("", id="lang-note")

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        lv = self.query_one("#lang-list", ListView)
        lv.clear()
        active = self.settings.language
        for code, name in available():
            mark = f"  [dim]({t('active')})[/]" if code == active else ""
            lv.append(ListItem(Static(f"{name}{mark}", markup=True), name=code))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        code = event.item.name if event.item else None
        if not code or code == self.settings.language:
            return
        self.settings.language = code
        preferences.save(self.settings)
        set_language(code)  # so the (active) marker tracks the new pick
        self._refresh()
        self.query_one("#lang-note", Static).update(t("Restart the app to apply the new language."))
        log.info("language = %s", code)

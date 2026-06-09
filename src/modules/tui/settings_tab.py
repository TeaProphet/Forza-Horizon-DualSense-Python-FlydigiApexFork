"""Settings tab: numeric tuning inputs with live-save."""
import logging

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, Switch

from modules import preferences

log = logging.getLogger("fhds")

SETTING_SECTIONS = [
    ("Pedals / deadzones", [
        ("accel_deadzone", "Accel deadzone", 0, 255),
        ("brake_deadzone", "Brake deadzone", 0, 255),
    ]),
    ("Brake (left trigger)", [
        ("brake_baseline_force", "Baseline force", 0, 255),
        ("brake_max_force",      "Max force",     0, 255),
        ("brake_curve",          "Curve",         0.1, 20.0),
        ("handbrake_bonus",      "Handbrake bonus", 0, 255),
    ]),
    ("Throttle (right trigger)", [
        ("throttle_baseline_force", "Baseline force", 0, 255),
        ("throttle_max_force",      "Max force",     0, 255),
        ("throttle_curve",          "Curve",         0.1, 20.0),
    ]),
    ("ABS", [
        ("abs_brake_threshold",         "Brake threshold",         0, 255),
        ("abs_min_speed_kmh",           "Min speed (km/h)",        0.0, 500.0),
        ("abs_slip_ratio_threshold",    "Slip ratio threshold",    0.0, 10.0),
        ("abs_combined_slip_threshold", "Combined slip threshold", 0.0, 10.0),
        ("abs_freq",                    "Frequency (Hz)",          0, 255),
        ("abs_amp",                     "Amplitude",               0, 255),
    ]),
    ("Rev limiter", [
        ("rev_limit_ratio",   "Trigger at RPM ratio", 0.0, 1.0),
        ("rev_limit_freq",    "Frequency (Hz)",       0, 255),
        ("rev_limit_amp",     "Amplitude",            0, 255),
        ("rev_limit_hold_ms", "Hold (ms)",            0.0, 1000.0),
    ]),
    ("Gear shift thump", [
        ("gear_shift_freq",        "Frequency (Hz)", 0, 255),
        ("gear_shift_amp",         "Amplitude",      0, 255),
        ("gear_shift_duration_ms", "Duration (ms)",  0.0, 2000.0),
    ]),
    ("Telemetry Rumble (Flydigi Apex 4/5)", [
        ("rumble_speed_scale",      "Speed scale",            0.0, 10.0),
        ("rumble_slip_scale",       "Tire slip scale",        0.0, 255.0),
        ("rumble_slip_deadzone",    "Tire slip deadzone",     0.0, 1.0),
        ("rumble_brake_scale",      "Brake scale",            0.0, 10.0),
        ("rumble_rpm_scale",        "Engine RPM scale",       0.0, 255.0),
        ("rumble_rpm_threshold",    "Engine RPM threshold",   0.0, 1.0),
        ("rumble_surface_scale",    "Surface/bump scale",     0.0, 255.0),
        ("rumble_surface_deadzone", "Surface deadzone",       0.0, 1.0),
        ("rumble_curb_scale",       "Curb/track strip scale", 0.0, 255.0),
        ("rumble_max_intensity",    "Max intensity",          0, 255),
        ("enable_split_rumble",     "Split HID reports (If you have 2 controllers displayed in Steam - enable it, else - disable)", None, None),
    ]),
    ("Misc", [
        ("enable_emulation_trigger", "Auto-activate Flydigi DualSense emulation mode", None, None),
        ("startup_pulse_force",  "Startup pulse force",    0, 255),
        ("enable_rumble",        "Rumble motors from telemetry (Flydigi Apex 4/5 / non-Steam rumble)", None, None),
        ("enable_reconnect",     "Auto-reconnect controller (disable for HidHide)", None, None),
        ("reconnect_interval_s", "Reconnect interval (s)", 0.1, 60.0),
    ]),
]

SETTING_RANGES = {a: (lo, hi) for _, fields in SETTING_SECTIONS
                  for a, _, lo, hi in fields if lo is not None and hi is not None}


def _fmt_range(lo, hi):
    if isinstance(lo, int) and isinstance(hi, int):
        return f"{lo}-{hi}"
    return f"{lo:g}-{hi:g}"


class SettingsTab(VerticalScroll):
    DEFAULT_CSS = """
    SettingsTab { width: 1fr; height: 1fr; padding: 1 2; }
    SettingsTab Label.section { text-style: bold; color: $accent; padding: 1 0 0 1; }
    SettingsTab .row { height: 3; width: 1fr; align-vertical: middle; padding: 0 1; }
    SettingsTab .row Label { width: 1fr; height: 3; content-align: left middle; }
    SettingsTab .row Input { width: 16; min-width: 10; max-width: 20; height: 3; }
    SettingsTab .row Switch { margin-right: 2; }
    SettingsTab #reset-settings { width: 1fr; margin: 2 0 1 0; }
    SettingsTab #btn-test-rumble { height: 3; width: auto; min-width: 15; margin-left: 2; }
    """

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        for section, fields in SETTING_SECTIONS:
            yield Label(section, classes="section")
            for attr, label, lo, hi in fields:
                value = getattr(self.settings, attr, None)
                if value is None:
                    continue
                if isinstance(value, bool):
                    with Horizontal(classes="row"):
                        yield Switch(value=value, id=attr)
                        yield Label(label)
                        if attr == "enable_rumble":
                            yield Button("Test Rumble", id="btn-test-rumble", variant="primary")
                    continue
                input_type = "integer" if isinstance(value, int) else "number"
                with Horizontal(classes="row"):
                    yield Label(f"{label} ({_fmt_range(lo, hi)})")
                    yield Input(value=str(value), id=f"set-{attr}", type=input_type)
        yield Button("Reset to defaults", id="reset-settings", variant="error")

    def on_switch_changed(self, event: Switch.Changed):
        attr = event.switch.id
        if not attr or not hasattr(self.settings, attr):
            return
        if getattr(self.settings, attr) != event.value:
            setattr(self.settings, attr, event.value)
            preferences.save(self.settings)
            log.info("%s = %s", attr, event.value)
        # Push live every time - profile-load/reset flow sets widget values
        # after the settings object is already mutated, so we'd otherwise miss
        # propagating to the running DualSense instance.
        self._push_live(attr, event.value)

    def _push_live(self, attr: str, value) -> None:
        """Push settings that DualSense captures at construction to the running
        instance so the toggle takes effect without restarting the backend."""
        ds = getattr(self.app, "_ds", None)
        if ds is None:
            return
        if attr == "enable_reconnect":
            ds.set_reconnect_enabled(value)
        elif attr == "reconnect_interval_s":
            ds.set_reconnect_interval(value)

    def on_input_submitted(self, event: Input.Submitted):
        self._commit(event.input, strict=True)

    def on_input_changed(self, event: Input.Changed):
        # Live-save on every keystroke that parses cleanly; partial input is ignored.
        self._commit(event.input, strict=False)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "reset-settings":
            preferences.reset(self.settings)
            self.app.refresh_setting_widgets()
            log.info("Settings reset to defaults.")
        elif event.button.id == "btn-test-rumble":
            if hasattr(self.app, "test_rumble"):
                self.app.test_rumble()

    def _commit(self, widget: Input, strict: bool) -> None:
        if not widget.id or not widget.id.startswith("set-"):
            return
        attr = widget.id[4:]
        if not hasattr(self.settings, attr):
            return
        current = getattr(self.settings, attr)
        raw = widget.value.strip()
        if not raw:
            return
        try:
            if isinstance(current, bool):
                new = raw.lower() in ("1", "true", "yes", "on")
            elif isinstance(current, int):
                new = int(float(raw))
            elif isinstance(current, float):
                new = float(raw)
            else:
                new = raw
        except ValueError:
            if strict:
                widget.value = str(current)
            return
        rng = SETTING_RANGES.get(attr)
        if rng and isinstance(new, (int, float)) and not isinstance(new, bool):
            lo, hi = rng
            clamped = max(lo, min(hi, new))
            if isinstance(current, int):
                clamped = int(clamped)
            if clamped != new:
                if strict:
                    new = clamped
                    widget.value = str(new)
                else:
                    return
        if new != current:
            setattr(self.settings, attr, new)
            preferences.save(self.settings)
            log.info("%s = %s", attr, new)
        # Always push live (see on_switch_changed for the profile-load reason).
        self._push_live(attr, new)

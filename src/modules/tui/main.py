"""Textual TUI: tabbed Controls / Logs.

Toggles mutate the live Settings instance the loop reads each frame, so changes
take effect immediately without a restart.
"""
import logging
import threading
import time

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from modules import dualsense, loop, preferences, udplistener
from modules.dualsense.triggers import off, vibration
from modules.update_check import log_latest_commit_age

HAPTIC_FREQ_HZ = 40
HAPTIC_AMP_ON = 200
HAPTIC_AMP_OFF = 120
HAPTIC_DURATION_S = 0.10

LOG_LEVELS = ("WARNING", "INFO", "DEBUG")
DEFAULT_LOG_LEVEL = "INFO"

log = logging.getLogger("fh5ds")

TOGGLES = [
    ("enable_brake_resistance",     "Brake resistance"),
    ("enable_handbrake_bonus",      "Handbrake bonus"),
    ("enable_abs",                  "ABS pulse"),
    ("enable_throttle_resistance",  "Throttle resistance"),
    ("enable_rev_limiter",          "Rev limiter"),
    ("enable_gear_shift",           "Gear shift thump"),
]

# (section title, [(attr, label), ...])
SETTING_SECTIONS = [
    ("Pedals / deadzones", [
        ("accel_deadzone",          "Accel deadzone (0-255)"),
        ("brake_deadzone",          "Brake deadzone (0-255)"),
        ("brake_full_force_at",     "Brake → max force at"),
        ("throttle_full_force_at",  "Throttle → max force at"),
    ]),
    ("Brake (left trigger)", [
        ("brake_baseline_force",    "Baseline force"),
        ("brake_max_force",         "Max force"),
        ("brake_curve",             "Curve"),
        ("handbrake_bonus",         "Handbrake bonus"),
    ]),
    ("ABS", [
        ("abs_brake_threshold",         "Brake threshold"),
        ("abs_min_speed_kmh",           "Min speed (km/h)"),
        ("abs_slip_ratio_threshold",    "Slip ratio threshold"),
        ("abs_combined_slip_threshold", "Combined slip threshold"),
        ("abs_freq",                    "Frequency (Hz)"),
        ("abs_amp",                     "Amplitude"),
    ]),
    ("Throttle (right trigger)", [
        ("throttle_baseline_force", "Baseline force"),
        ("throttle_max_force",      "Max force"),
        ("throttle_curve",          "Curve"),
    ]),
    ("Rev limiter", [
        ("rev_limit_ratio", "Trigger at RPM ratio"),
        ("rev_limit_freq",  "Frequency (Hz)"),
        ("rev_limit_amp",   "Amplitude"),
    ]),
    ("Gear shift thump", [
        ("gear_shift_freq",         "Frequency (Hz)"),
        ("gear_shift_amp",          "Amplitude"),
        ("gear_shift_duration_ms",  "Duration (ms)"),
    ]),
    ("Misc", [
        ("startup_pulse_force",    "Startup pulse force"),
        ("reconnect_interval_s",   "Reconnect interval (s)"),
    ]),
]


class _LogToWidget(logging.Handler):
    def __init__(self, app: "TriggerTUI"):
        super().__init__()
        self._app = app

    def emit(self, record):
        try:
            msg = self.format(record)
            self._app.call_from_thread(self._app.write_log, msg)
        except Exception:
            pass


class TriggerTUI(App):
    CSS = """
    Screen { layout: vertical; }
    #status { dock: top; height: 1; padding: 0 1; background: $boost; color: $text; }
    TabbedContent { height: 1fr; }
    .controls-row { height: 3; }
    .controls-col { padding: 1 2; }
    Switch { margin-right: 2; }
    Label.title { text-style: bold; padding-bottom: 1; }
    Label.section { text-style: bold; color: $accent; padding: 1 0 0 0; }
    Label.note { color: $text-muted; padding-top: 1; text-style: italic; }
    Label.field { width: 32; padding: 1 1 0 0; }
    .setting-row { height: 3; }
    Input { width: 20; }
    RichLog { padding: 0 1; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("p", "toggle_pause", "Pause logs"),
        ("l", "cycle_level", "Log level"),
        ("c", "clear_logs", "Clear logs"),
    ]

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._stop = threading.Event()
        self._thread = None
        self._ds = None
        self._listener_cm = None
        self._listener = None
        self._started = False
        self._paused = False
        self._level_idx = LOG_LEVELS.index(DEFAULT_LOG_LEVEL)
        self._handler = None
        self._status_thread = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status")
        with TabbedContent(initial="tab-controls"):
            with TabPane("Controls", id="tab-controls"):
                with Horizontal():
                    with Vertical(classes="controls-col"):
                        yield Label("Animations", classes="title")
                        for attr, label in TOGGLES:
                            with Horizontal(classes="controls-row"):
                                yield Switch(value=getattr(self.settings, attr), id=attr)
                                yield Label(label)
                        yield Label("Unofficial fan project.", classes="note")
            with TabPane("Settings", id="tab-settings"):
                with VerticalScroll(classes="controls-col"):
                    for section_title, fields in SETTING_SECTIONS:
                        yield Label(section_title, classes="section")
                        for attr, label in fields:
                            current = getattr(self.settings, attr, None)
                            if current is None:
                                continue
                            input_type = "integer" if isinstance(current, int) and not isinstance(current, bool) else "number"
                            with Horizontal(classes="setting-row"):
                                yield Label(label, classes="field")
                                yield Input(
                                    value=str(current),
                                    id=f"set-{attr}",
                                    type=input_type,
                                )
                    yield Label("Changes apply immediately and persist to user_preferences.json.", classes="note")
            with TabPane("Logs", id="tab-logs"):
                yield RichLog(id="logs", highlight=False, markup=False, wrap=True, max_lines=2000)
        yield Footer()

    # MARK: Mount — wire logging, open hardware, start loop
    def on_mount(self):
        self.title = "FH5 DualSense"
        self.sub_title = f"UDP {self.settings.udp_host}:{self.settings.udp_port}"

        root = logging.getLogger()
        root.handlers.clear()
        self._handler = _LogToWidget(self)
        self._handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(self._handler)
        root.setLevel(self._current_level())

        self._refresh_status()
        log_latest_commit_age()
        log.info("Starting controller and telemetry listener...")

        self.call_after_refresh(self._start_backend)
        self._start_status_ticker()

    def _start_backend(self):
        if self._started:
            return
        self._started = True
        try:
            s = self.settings
            self._ds = dualsense.DualSense(
                startup_pulse_force=s.startup_pulse_force,
                enable_startup_pulse=s.enable_startup_pulse,
                reconnect_interval_s=s.reconnect_interval_s,
            )
            self._ds.open()
            self._listener_cm = udplistener.UDPListener(s.udp_host, s.udp_port, s.udp_timeout)
            self._listener = self._listener_cm.__enter__()
            log.info("Listening on %s:%d", s.udp_host, s.udp_port)
            log.info("In FH5: HUD & Gameplay -> Data Out: ON, IP %s, Port %d", s.udp_host, s.udp_port)

            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
        except Exception as exc:
            log.exception("Backend startup failed")
            self._notify_status(f"Backend failed: {exc}")

    def _run_loop(self) -> None:
        try:
            loop.run(self._ds, self._listener, self.settings, stop_event=self._stop)
        finally:
            if not self._stop.is_set():
                self.call_from_thread(self.exit)

    def on_unmount(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._listener_cm:
            try:
                self._listener_cm.__exit__(None, None, None)
            except Exception:
                pass
        if self._ds:
            self._ds.close()

    # MARK: Status bar — controller state, pause indicator, log level
    def _start_status_ticker(self) -> None:
        def tick():
            while not self._stop.is_set():
                try:
                    self.call_from_thread(self._refresh_status)
                except Exception:
                    return
                time.sleep(1.0)

        self._status_thread = threading.Thread(target=tick, daemon=True)
        self._status_thread.start()

    def _refresh_status(self) -> None:
        ds_state = "connected" if (self._ds and self._ds.connected) else "waiting"
        bits = [
            f"DualSense: {ds_state}",
            f"Logs: {self._current_level_name()}",
            "PAUSED" if self._paused else "live",
        ]
        try:
            self.query_one("#status", Static).update("  •  ".join(bits))
        except Exception:
            pass

    def _notify_status(self, msg: str) -> None:
        try:
            self.query_one("#status", Static).update(msg)
        except Exception:
            pass

    # MARK: Log helpers
    def _current_level(self) -> int:
        return getattr(logging, LOG_LEVELS[self._level_idx])

    def _current_level_name(self) -> str:
        return LOG_LEVELS[self._level_idx]

    def write_log(self, msg: str) -> None:
        try:
            self.query_one("#logs", RichLog).write(msg, scroll_end=not self._paused)
        except Exception:
            pass

    # MARK: Actions
    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        self._refresh_status()

    def action_cycle_level(self) -> None:
        self._level_idx = (self._level_idx + 1) % len(LOG_LEVELS)
        logging.getLogger().setLevel(self._current_level())
        self._refresh_status()
        log.info("Log level: %s", self._current_level_name())

    def action_clear_logs(self) -> None:
        try:
            self.query_one("#logs", RichLog).clear()
        except Exception:
            pass

    # MARK: Toggle switch — mutate settings + haptic feedback (no log spam)
    def on_switch_changed(self, event: Switch.Changed) -> None:
        attr = event.switch.id
        if attr and hasattr(self.settings, attr):
            setattr(self.settings, attr, event.value)
            preferences.save(self.settings)
            self._haptic_pulse(event.value)

    # MARK: Settings input — commit on Enter
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._commit_input(event.input)

    def _commit_input(self, widget: Input) -> None:
        if not widget.id or not widget.id.startswith("set-"):
            return
        attr = widget.id[4:]
        if not hasattr(self.settings, attr):
            return
        current = getattr(self.settings, attr)
        raw = widget.value.strip()
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
            widget.value = str(current)
            return
        if new == current:
            return
        setattr(self.settings, attr, new)
        preferences.save(self.settings)
        log.info("%s = %s", attr, new)

    def _haptic_pulse(self, on: bool) -> None:
        if not self._ds or not self._ds.connected:
            return
        threading.Thread(target=self._do_haptic, args=(on,), daemon=True).start()

    def _do_haptic(self, on: bool) -> None:
        amp = HAPTIC_AMP_ON if on else HAPTIC_AMP_OFF
        v = vibration(HAPTIC_FREQ_HZ, amp)
        self._ds.set(v, v)
        time.sleep(HAPTIC_DURATION_S)
        self._ds.set(off(), off())

import logging
import struct
import threading
import time
import zlib

import hid

from .triggers import M_RIGID, off, rigid

log = logging.getLogger("fh5ds.dualsense")

VENDOR_ID = 0x054C
PRODUCT_IDS = (0x0CE6, 0x0DF2)  # DualSense, DualSense Edge

# valid_flag0 bits:
#   0x01 = right motor enable
#   0x02 = left motor enable
#   0x04 = right trigger
#   0x08 = left trigger
#
# APEX 4/5 FIX: we always include motor bits (0x01|0x02) so the Apex 4/5 firmware
# actually activates the vibration motors. 
TRIG_FLAGS = 0x01 | 0x02 | 0x04 | 0x08

MOTOR_RIGHT_OFFSET_USB = 3
MOTOR_LEFT_OFFSET_USB  = 4
MOTOR_RIGHT_OFFSET_BT  = 4   
MOTOR_LEFT_OFFSET_BT   = 5

USB = {"rid": 0x02, "flags": 1, "r": 11, "l": 22, "size": 48, "bt": False,
       "mr": MOTOR_RIGHT_OFFSET_USB, "ml": MOTOR_LEFT_OFFSET_USB}
BT  = {"rid": 0x31, "flags": 2, "r": 12, "l": 23, "size": 78, "bt": True,
       "mr": MOTOR_RIGHT_OFFSET_BT,  "ml": MOTOR_LEFT_OFFSET_BT}

# ---------------------------------------------------------------------------
# Rumble engine - derives motor intensities from Forza UDP telemetry
# Based on your provided "main (1).py" script logic
# ---------------------------------------------------------------------------
class RumbleEngine:
    @classmethod
    def compute(cls, t: dict, s = None) -> tuple[int, int]:
        speed_scale = s.rumble_speed_scale if s is not None else 0.0
        slip_scale = s.rumble_slip_scale if s is not None else 20.0
        slip_deadzone = s.rumble_slip_deadzone if s is not None else 0.10
        brake_scale = s.rumble_brake_scale if s is not None else 0.2
        rpm_scale = s.rumble_rpm_scale if s is not None else 15.0
        rpm_threshold = s.rumble_rpm_threshold if s is not None else 0.80
        surface_scale = s.rumble_surface_scale if s is not None else 40.0
        surface_deadzone = s.rumble_surface_deadzone if s is not None else 0.05
        curb_scale = s.rumble_curb_scale if s is not None else 50.0
        max_intensity = s.rumble_max_intensity if s is not None else 180

        speed_kmh = t.get("speed", 0.0)
        rpm       = t.get("current_engine_rpm", t.get("rpm", 0.0))
        max_rpm   = t.get("engine_max_rpm", t.get("max_rpm", 1.0)) or 1.0
        brake     = t.get("brake", 0)

        # 1. Tire Slip (traction loss / understeer / oversteer)
        slip_keys = ["tire_slip_ratio_fl", "tire_slip_ratio_fr", "tire_slip_ratio_rl", "tire_slip_ratio_rr"]
        total_slip = 0.0
        for k in slip_keys:
            val = abs(t.get(k, 0.0))
            if val > slip_deadzone:
                total_slip += (val - slip_deadzone)

        # 2. Surface Rumble (gravel, dirt, road surface bumps)
        surface_keys = ["surface_rumble_fl", "surface_rumble_fr", "surface_rumble_rl", "surface_rumble_rr"]
        total_surface = 0.0
        for k in surface_keys:
            val = abs(t.get(k, 0.0))
            if val > surface_deadzone:
                total_surface += (val - surface_deadzone)

        # 3. Curb / Rumble Strip detection
        curb_keys = ["wheel_on_rumble_strip_fl", "wheel_on_rumble_strip_fr", "wheel_on_rumble_strip_rl", "wheel_on_rumble_strip_rr"]
        total_curb = sum(abs(t.get(k, 0.0)) for k in curb_keys)

        # Heavy (Left) Motor - ABS / braking, rough surfaces, and tire slip
        left = (
            speed_kmh * speed_scale
            + total_surface * surface_scale
            + total_slip * slip_scale
            + brake * brake_scale
        )

        # Light (Right) Motor - High engine RPM, track curbs, and fine tire slip
        rpm_ratio = rpm / max_rpm
        right = 0.0
        if rpm_ratio > rpm_threshold:
            right = ((rpm_ratio - rpm_threshold) / (1.0 - rpm_threshold)) * rpm_scale
        
        right += total_curb * curb_scale
        right += total_slip * (slip_scale * 0.15)

        ml = int(min(left,  max_intensity))
        mr = int(min(right, max_intensity))
        return ml, mr

def _find_gamepad(retries=10, delay=0.5):
    for i in range(retries):
        devices = hid.enumerate(VENDOR_ID, 0)
        for d in devices:
            if (d.get("product_id") in PRODUCT_IDS
                    and d.get("usage_page", 1) == 1
                    and d.get("usage", 5) == 5):
                return d
        for d in devices:
            if d.get("product_id") in PRODUCT_IDS:
                log.warning("Usage page/usage not found, picking first matching VID/PID: %s", d.get("path"))
                return d
        if i < retries - 1:
            time.sleep(delay)
    raise RuntimeError("DualSense gamepad not found.")

def _is_bluetooth(info):
    bus_type = info.get("bus_type")
    if bus_type is not None:
        return bus_type == 2
    path = info.get("path", b"")
    if isinstance(path, str):
        path = path.encode()
    return b"BTHENUM" in path.upper()

def _normalize_path(path) -> str:
    if not path:
        return ""
    if isinstance(path, bytes):
        path = path.decode(errors="ignore")
    return path.lower().replace("/", "\\")


def _is_non_dualsense_controller(d: dict) -> bool:
    vid = d.get("vendor_id")
    pid = d.get("product_id")
    # Check if it is a DualSense or DualSense Edge controller
    if vid == 0x054C and pid in (0x0CE6, 0x0DF2):
        return False
    # Check standard controller HID usages
    usage_page = d.get("usage_page")
    usage = d.get("usage")
    if usage_page == 1 and usage in (4, 5):
        return True
    # Fallback to string matching on product name
    prod = d.get("product_string") or ""
    if isinstance(prod, str):
        prod_lower = prod.lower()
        if "controller" in prod_lower or "gamepad" in prod_lower or "flydigi" in prod_lower:
            return True
    return False


class DualSense:
    def __init__(self, startup_pulse_force: int = 180, enable_startup_pulse: bool = True,
                 enable_reconnect: bool = False, reconnect_interval_s: float = 5.0,
                 settings = None):
        self.dev = None
        self.lay = USB
        self._lock = threading.Lock()
        self._left = self._right = off()
        self._motor_left = self._motor_right = 0
        self._dirty = False
        self._forcing = False
        self._running = False
        self._thread = None
        self._pulse_force = startup_pulse_force
        self._enable_startup_pulse = enable_startup_pulse
        self._enable_reconnect = enable_reconnect
        self._reconnect_interval_s = reconnect_interval_s
        self._settings = settings

    @property
    def connected(self) -> bool:
        return self.dev is not None and self._running

    def open(self):
        self._running = True
        self._thread = threading.Thread(target=self._io, daemon=True)
        self._thread.start()

    def close(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None
        if self.dev:
            try:
                self.dev.write(self._build(off(), off(), 0, 0, mode="all"))
            except Exception:
                pass
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None

    def _force_motors(self, ml: int, mr: int, duration: float = 0.3):
        """Directly write motor values for testing, bypassing the dirty flag."""
        with self._lock:
            self._forcing = True
            self._motor_left, self._motor_right = ml, mr
            self._dirty = True
        time.sleep(duration)
        with self._lock:
            self._motor_left = self._motor_right = 0
            self._dirty = True
            self._forcing = False

    def set_reconnect_enabled(self, enabled: bool):
        self._enable_reconnect = enabled
        log.info("DualSense auto-reconnect set to %s", enabled)

    def set_reconnect_interval(self, interval: float):
        self._reconnect_interval_s = interval
        log.info("DualSense reconnect interval set to %.1fs", interval)

    def set(self, left, right, telemetry: dict | None = None, settings = None):
        ml = mr = 0
        if telemetry is not None:
            ml, mr = RumbleEngine.compute(telemetry, settings)
        with self._lock:
            self._left, self._right = left, right
            if not self._forcing:
                self._motor_left, self._motor_right = ml, mr
            self._dirty = True

    def _io(self):
        from . import hidhide
        has_hidhide = hidhide.is_detected()
        persistent_mode = False
        has_connected_once = False
        last_presence_check = 0.0

        # Initialize the set of known non-DualSense controller paths for monitoring connections
        try:
            known_controllers = {d.get("path") for d in hid.enumerate() if _is_non_dualsense_controller(d)}
        except Exception:
            known_controllers = set()

        while self._running:
            if self.dev is None:
                # Monitor for new non-DualSense controller connections to cycle emulation trigger
                try:
                    current_controllers = {d.get("path") for d in hid.enumerate() if _is_non_dualsense_controller(d)}
                    new_controllers = current_controllers - known_controllers
                    known_controllers = current_controllers
                    
                    if new_controllers:
                        log.info("New non-DualSense controller detected: cycling emulation trigger...")
                        from .. import emulation_trigger
                        emulation_trigger.stop_trigger()
                        if self._settings:
                            emulation_trigger.start_trigger(self._settings)
                        time.sleep(0.5)
                except Exception as monitor_err:
                    log.debug("Error monitoring controllers: %s", monitor_err)

                # Ensure background trigger is started when we are looking/waiting for the controller
                if self._settings:
                    try:
                        from .. import emulation_trigger
                        emulation_trigger.start_trigger(self._settings)
                    except Exception as trigger_err:
                        log.warning("Failed to start emulation trigger in IO loop: %s", trigger_err)

                try:
                    info = _find_gamepad(retries=1, delay=0.0)
                    dev = hid.device()
                    dev.open_path(info["path"])
                    dev.set_nonblocking(True)
                    
                    self.lay = BT if _is_bluetooth(info) else USB
                    self.dev = dev
                    self._device_path = info["path"]
                    last_presence_check = time.monotonic()
                    
                    log.info("DualSense connected (%s) at %s", "BT" if self.lay["bt"] else "USB", info["path"])
                    has_connected_once = True
                    
                    if has_hidhide:
                        persistent_mode = True
                        log.info("HidHide detected: latched into persistent mode (ignoring read/write errors)")

                    if self._enable_startup_pulse:
                        pulse = rigid(self._pulse_force)
                        try:
                            dev.write(self._build(pulse, pulse, 0, 0, mode="triggers"))
                            time.sleep(0.2)
                            dev.write(self._build(off(), off(), 0, 0, mode="triggers"))
                            log.info("Motor test: sending rumble at intensity 200 for 0.3s...")
                            dev.write(self._build(off(), off(), 200, 200, mode="motors"))
                            time.sleep(0.3)
                            dev.write(self._build(off(), off(), 0, 0, mode="motors"))
                            log.info("Motor test complete.")
                        except Exception as write_err:
                            log.warning("Startup pulse/motor test failed: %s", write_err)

                except Exception:
                    self.dev = None
                    if has_connected_once and not self._enable_reconnect:
                        log.info("Controller disconnected and auto-reconnect is disabled. Stopping trigger thread.")
                        try:
                            from .. import emulation_trigger
                            emulation_trigger.stop_trigger()
                        except Exception:
                            pass
                        self._running = False
                        break
                    time.sleep(self._reconnect_interval_s if self._enable_reconnect else 1.0)
                    continue

            # Connected! Process loop
            try:
                try:
                    self.dev.read(self.lay["size"])
                except OSError:
                    # Ignore read errors as some controllers/configurations do not support reading or raise constant error
                    pass

                # Periodically verify physical presence of the device path
                now = time.monotonic()
                if now - last_presence_check > 1.0:
                    last_presence_check = now
                    still_present = False
                    try:
                        target_path = _normalize_path(self._device_path)
                        still_present = any(_normalize_path(d.get("path")) == target_path for d in hid.enumerate(VENDOR_ID, 0))
                    except Exception:
                        pass
                    if not still_present:
                        raise OSError("Controller physically unplugged")

                with self._lock:
                    if not self._dirty:
                        time.sleep(0.001)
                        continue
                    left  = self._left
                    right = self._right
                    ml    = self._motor_left
                    mr    = self._motor_right
                    self._dirty = False

                if getattr(self._settings, "enable_split_rumble", True):
                    self.dev.write(self._build(left, right, 0, 0, mode="triggers"))
                    self.dev.write(self._build(off(), off(), ml, mr, mode="motors"))
                else:
                    self.dev.write(self._build(left, right, ml, mr, mode="all"))

            except Exception as e:
                if persistent_mode:
                    time.sleep(0.01)
                    continue
                else:
                    log.warning("HID error: %s", e)
                    try:
                        self.dev.close()
                    except Exception:
                        pass
                    self.dev = None
                    
                    try:
                        from .. import emulation_trigger
                        emulation_trigger.stop_trigger()
                    except Exception:
                        pass

                    if not self._enable_reconnect:
                        log.info("Controller disconnected and auto-reconnect is disabled. Stopping trigger thread.")
                        self._running = False
                        break
                    
                    time.sleep(self._reconnect_interval_s)

    def _build(self, left, right, motor_left: int = 0, motor_right: int = 0, mode: str = "all"):
        L   = self.lay
        buf = bytearray(L["size"])

        buf[0] = L["rid"]
        if L["bt"]:
            buf[1] = 0x02

        if mode == "motors":
            flags = 0x01 | 0x02
        elif mode == "triggers":
            flags = 0x04 | 0x08
        else:
            flags = 0x01 | 0x02 | 0x04 | 0x08

        buf[L["flags"]] = flags
        buf[L["flags"] + 1] = 0xF7    # valid_flag1 - critical for rumble on emulated DualSense (Apex 4/5)
        buf[L["flags"] + 45] = 255    # led_green - critical for rumble on emulated DualSense (Apex 4/5)

        mr_val = max(0, min(255, motor_right))
        ml_val = max(0, min(255, motor_left))
        buf[L["mr"]] = mr_val
        buf[L["ml"]] = ml_val

        if (mr_val or ml_val) and mode == "motors":
            log.debug("HID write: motor_left=%d motor_right=%d (offsets ml=%d mr=%d)",
                      ml_val, mr_val, L["ml"], L["mr"])

        for pos, (trig_mode, params) in ((L["r"], right), (L["l"], left)):
            buf[pos] = trig_mode
            for i, p in enumerate(params):
                buf[pos + 1 + i] = max(0, min(255, p))

        if L["bt"]:
            struct.pack_into("<I", buf, 74, zlib.crc32(b"\xA2" + bytes(buf[:74])))

        return bytes(buf)
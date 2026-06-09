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

def _find_gamepad():
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
    raise RuntimeError("DualSense gamepad not found.")

def _is_bluetooth(info):
    bus_type = info.get("bus_type")
    if bus_type is not None:
        return bus_type == 2
    path = info.get("path", b"")
    if isinstance(path, str):
        path = path.encode()
    return b"BTHENUM" in path.upper()

class DualSense:
    def __init__(self, startup_pulse_force: int = 180, enable_startup_pulse: bool = True,
                 enable_reconnect: bool = False, reconnect_interval_s: float = 5.0):
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
        # Stored for future use; not yet wired to reconnect logic.
        self._enable_reconnect = enable_reconnect
        self._reconnect_interval_s = reconnect_interval_s

    @property
    def connected(self) -> bool:
        return self.dev is not None and self._running

    def open(self):
        info = _find_gamepad()
        self.dev = hid.device()
        self.dev.open_path(info["path"])
        self.lay = BT if _is_bluetooth(info) else USB
        self.dev.set_nonblocking(True)
        log.info("DualSense connected (%s) at %s", "BT" if self.lay["bt"] else "USB", info["path"])
        self._running = True
        self._thread = threading.Thread(target=self._io, daemon=True)
        self._thread.start()

        if self._enable_startup_pulse:
            # Trigger pulse
            pulse = rigid(self._pulse_force)
            self.set(pulse, pulse)
            time.sleep(0.2)
            self.set(off(), off())
            # Motor rumble test - confirms the controller responds to motor bytes
            log.info("Motor test: sending rumble at intensity 200 for 0.3s...")
            self._force_motors(200, 200, duration=0.3)
            log.info("Motor test complete.")

    def close(self):
        if not self.dev:
            return
        self.set(off(), off())
        time.sleep(0.1)
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self.dev.close()
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
        while self._running:
            try:
                try:
                    self.dev.read(self.lay["size"])
                except OSError:
                    pass

                with self._lock:
                    if not self._dirty:
                        time.sleep(0.001)
                        continue
                    left  = self._left
                    right = self._right
                    ml    = self._motor_left
                    mr    = self._motor_right
                    self._dirty = False

                self.dev.write(self._build(left, right, 0, 0, mode="triggers"))
                self.dev.write(self._build(off(), off(), ml, mr, mode="motors"))

            except Exception:
                log.exception("HID write failed; stopping trigger thread")
                self._running = False
                break

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
import logging
import struct
import sys
import threading
import time
import zlib

# PyPI's hidapi Linux wheel uses libusb, which can't claim the gamepad interface
# (hid-playstation kernel driver owns it). Use a direct /dev/hidraw shim instead.
if sys.platform.startswith("linux"):
    from . import _hidraw as hid
else:
    import hid

from .triggers import M_RIGID, off

log = logging.getLogger("fhds.dualsense")

VENDOR_ID = 0x054C
PRODUCT_IDS = (0x0CE6, 0x0DF2)  # DualSense, DualSense Edge

# valid_flag0: 0x01 (R motor), 0x02 (L motor), 0x04 (R trigger), 0x08 (L trigger).
# Some firmware needs motor bits set for trigger bits to be processed.
TRIG_FLAGS = 0x01 | 0x02 | 0x04 | 0x08

# MARK: Layout maps — byte offsets per transport
# vf1 = valid_flag1, psav = power_save_control
USB = {"rid": 0x02, "flags": 1, "vf1": 2, "psav": 10, "r": 11, "l": 22, "size": 64, "bt": False}
BT  = {"rid": 0x31, "flags": 2, "vf1": 3, "psav": 11, "r": 12, "l": 23, "size": 78, "bt": True}

# Precomputed CRC of the BT report-header byte 0xA2. zlib.crc32(data, value)
# resumes from `value`, so this lets us CRC straight off the buffer without
# allocating "\xA2" + bytes(buf[:74]) on every write.
_BT_CRC_SEED = zlib.crc32(b"\xA2")


def _enumerate_dualsenses():
    return [d for d in hid.enumerate(VENDOR_ID, 0)
            if d.get("product_id") in PRODUCT_IDS]


def _find_gamepad():
    """Pick the Game Pad HID interface (usage_page=1, usage=5) or None.
    Audio/sensor interfaces share VID/PID and silently drop trigger writes."""
    devices = _enumerate_dualsenses()
    for d in devices:
        if d.get("usage_page", 1) == 1 and d.get("usage", 5) == 5:
            return d
    return devices[0] if devices else None


def _is_bluetooth(info):
    """Detect BT across hidapi backends.

    bus_type values seen in the wild:
      - hidapi-windows:   USB=1, Bluetooth=2
      - hidapi-libusb:    follows libusb (USB always)
      - hidapi-hidraw (Linux): BUS_USB=3, BUS_BLUETOOTH=5
    """
    bus_type = info.get("bus_type")
    if bus_type in (2, 5):
        return True
    if bus_type in (1, 3):
        return False
    path = info.get("path", b"")
    if isinstance(path, str):
        path = path.encode()
    # Linux hidraw nodes don't carry bus info in the path; fall back to USB.
    return b"BTHENUM" in path.upper() or b"BLUETOOTH" in path.upper()


def _log_open_failure(err) -> None:
    # hidapi's "open failed" is opaque; on Linux it almost always means the
    # hidraw node is root-only because the udev rule isn't installed.
    if sys.platform.startswith("linux"):
        log.error(
            "DualSense open failed (%s). Install the udev rule:\n"
            "  sudo cp packaging/linux/70-dualsense.rules /etc/udev/rules.d/\n"
            "  sudo udevadm control --reload-rules && sudo udevadm trigger\n"
            "Then unplug/replug (USB) or re-pair (Bluetooth).", err,
        )
    else:
        log.warning("DualSense open failed (%s) — another app may be holding it open.", err)


class DualSense:
    """Triggers-only DualSense writer. Steam keeps rumble bits untouched.

    Resilient: starts without a controller and retries every
    ``reconnect_interval_s`` seconds. Drops writes silently while disconnected.
    """

    def __init__(
        self,
        startup_pulse_force: int = 180,
        enable_startup_pulse: bool = True,
        reconnect_interval_s: float = 5.0,
    ):
        self.dev = None
        self.dev_path = None
        self.lay = USB
        self._lock = threading.Lock()
        self._left = self._right = off()
        self._dirty = False
        self._running = False
        self._thread = None
        # Signalled by set() and close() so the I/O thread sleeps until a new
        # frame is ready instead of busy-polling at 1 kHz.
        self._wake = threading.Event()
        self._pulse_force = startup_pulse_force
        self._enable_startup_pulse = enable_startup_pulse
        self._reconnect_interval = reconnect_interval_s
        self._open_hinted = False
        self._waiting_hinted = False
        self._last_attempt = -1e9
        # Idle-input watchdog. DualSense streams input reports continuously
        # (hundreds of Hz). When the controller drops, the stream stops and
        # the nonblocking read returns empty for `_input_idle_timeout`.
        self._input_idle_timeout = 3.0
        self._last_input_at = 0.0

    @property
    def connected(self) -> bool:
        return self.dev is not None

    def open(self):
        """Start the I/O thread. Never raises if the controller is absent."""
        self._running = True
        self._thread = threading.Thread(target=self._io, daemon=True)
        self._thread.start()

    def close(self):
        self._running = False
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._disconnect()

    def set(self, left, right):
        with self._lock:
            self._left, self._right, self._dirty = left, right, True
        self._wake.set()

    def _safe_write(self, buf) -> None:
        """Best-effort write — used for startup pulses, power-saver, and the
        off-pulse during disconnect, all of which run while the device may be
        about to go away."""
        try:
            self.dev.write(buf)
        except Exception:
            pass

    # MARK: connect / disconnect helpers
    def _try_connect(self) -> bool:
        info = _find_gamepad()
        if not info:
            if not self._waiting_hinted:
                log.info("Waiting for DualSense — retrying every %.0fs", self._reconnect_interval)
                self._waiting_hinted = True
            return False
        try:
            dev = hid.device()
            dev.open_path(info["path"])
            dev.set_nonblocking(True)
        except (OSError, IOError) as e:
            if not self._open_hinted:
                _log_open_failure(e)
                self._open_hinted = True
            return False
        self.dev = dev
        self.dev_path = info.get("path")
        self.lay = BT if _is_bluetooth(info) else USB
        self._open_hinted = self._waiting_hinted = False
        self._last_input_at = time.monotonic()
        log.info("DualSense connected (%s)", "BT" if self.lay["bt"] else "USB")

        if self._enable_startup_pulse:
            pulse = (M_RIGID, (0, self._pulse_force))
            self._safe_write(self._build(pulse, pulse))
            time.sleep(0.2)
            self._safe_write(self._build(off(), off()))
        # MARK: Power saver — one-shot at connect
        self._safe_write(self._build_power_saver())
        return True

    def _disconnect(self, reason: str = ""):
        was_connected = self.dev is not None
        if was_connected:
            self._safe_write(self._build(off(), off()))
            try:
                self.dev.close()
            except Exception:
                pass
        self.dev = None
        self.dev_path = None
        if was_connected:
            suffix = f" ({reason})" if reason else ""
            log.warning("DualSense disconnected%s — retrying every %.0fs",
                        suffix, self._reconnect_interval)

    # MARK: I/O thread — reconnect when missing, write when dirty, watchdog on idle input
    def _io(self):
        while self._running:
            now = time.monotonic()

            # --- Disconnected: throttle reconnect attempts ---
            if not self.connected:
                if now - self._last_attempt >= self._reconnect_interval:
                    self._last_attempt = now
                    self._try_connect()  # logs success / waiting / open-failure itself
                self._wake.wait(0.5)
                self._wake.clear()
                continue

            # --- Connected: drain one input report for the liveness watchdog.
            # timeout_ms=0 forces a truly nonblocking read — set_nonblocking()
            # is unreliable on Windows Bluetooth, where read() would otherwise
            # block until the BT stack times out (~30 s after a drop).
            try:
                data = self.dev.read(self.lay["size"], timeout_ms=0)
            except OSError as e:
                self._disconnect(f"read failed: {e}")
                continue
            if data:
                self._last_input_at = now
            elif now - self._last_input_at >= self._input_idle_timeout:
                self._disconnect(f"no input for {self._input_idle_timeout:.0f}s")
                continue

            # --- Write the latest queued frame, if any ---
            with self._lock:
                dirty, left, right = self._dirty, self._left, self._right
                self._dirty = False
            if dirty:
                try:
                    n = self.dev.write(self._build(left, right))
                except Exception as e:
                    self._disconnect(f"write failed: {e}")
                    continue
                if n is not None and n <= 0:
                    self._disconnect(f"write returned {n}")
                    continue

            # Sleep until set() queues a new frame, or wake to recheck watchdogs.
            self._wake.wait(0.5)
            self._wake.clear()

    def _new_report(self):
        L = self.lay
        buf = bytearray(L["size"])
        buf[0] = L["rid"]
        if L["bt"]:
            buf[1] = 0x02
        return buf

    def _finalize_bt_crc(self, buf):
        if self.lay["bt"]:
            crc = zlib.crc32(memoryview(buf)[:74], _BT_CRC_SEED)
            struct.pack_into("<I", buf, 74, crc)

    def _build(self, left, right):
        L = self.lay
        buf = self._new_report()
        buf[L["flags"]] = TRIG_FLAGS
        for pos, (mode, params) in ((L["r"], right), (L["l"], left)):
            buf[pos] = mode
            # params elements are already clamped to 0-255 by triggers.py;
            # bytearray slice-assignment accepts a tuple of ints directly.
            buf[pos + 1:pos + 1 + len(params)] = params[:10]
        self._finalize_bt_crc(buf)
        return buf  # hidapi accepts bytearray — skip the bytes() copy.

    def _build_power_saver(self):
        """Build a minimal HID report that enables the power-save flag only."""
        L = self.lay
        buf = self._new_report()
        buf[L["vf1"]] |= 0x02          # bit 1 = POWER_SAVE_CONTROL enable
        buf[L["psav"]] |= 0x10         # bit 4 = hardware power save
        self._finalize_bt_crc(buf)
        return buf

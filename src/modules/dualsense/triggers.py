"""DualSense adaptive trigger effects — KISS edition.

Design rule: normal trigger forces are capped well below 255 so the trigger
usually keeps physical travel free for vibration animations. Pedal input above
98% jumps straight to max trigger force inside the normal pedal ramp.

Right trigger (throttle), strict priority — only one effect at a time:
    1. Gear shift  -> short vibration burst
    2. Rev limiter -> 30 Hz vibration
    3. Throttle    -> exponential rigid resistance (baseline -> max)

Left trigger (brake): telemetry tire-slip pulse under ABS-like braking,
otherwise exponential rigid resistance, baseline -> max.
Handbrake adds a flat bonus.

Every effect has an enable_* switch in settings.py.
"""

import time

# --- Raw mode bytes ---
M_OFF   = 0x05
M_RIGID = 0x01
M_PULSE = 0x06
RAW_MAX = 255


def _clamp(v, hi=RAW_MAX):
    return max(0, min(hi, round(v)))


def off():
    return (M_OFF, 0, 0)

def rigid(force):
    return (M_RIGID, 0, _clamp(force))

def vibration(freq_hz, amplitude):
    return (M_PULSE, _clamp(freq_hz), _clamp(amplitude))


class TriggerAnimation:
    """Computes (left, right) trigger output from FH5 telemetry each frame."""

    def __init__(self):
        self._prev_gear = 0
        self._shift_until = 0.0

    def update(self, t: dict, s) -> tuple:
        if not t.get("on", False):
            return off(), off()
        now = time.monotonic()
        return self._brake(t, s), self._throttle(t, s, now)

    # --- Left trigger: brake -------------------------------------------------

    def _brake(self, t, s):
        brake = t.get("brake", 0)

        if self._abs_active(t, s, brake):
            return vibration(s.abs_freq, s.abs_amp)

        if not s.enable_brake_resistance:
            if s.enable_handbrake_bonus and t.get("handbrake", 0):
                return rigid(s.handbrake_bonus)
            return off()

        # Always hold baseline so the trigger never toggles off<->rigid (no
        # "machine gun" jitter near the deadzone).
        if brake < s.brake_deadzone:
            force = s.brake_baseline_force
        else:
            force = self._pedal_force(
                brake,
                s.brake_deadzone,
                s.brake_baseline_force,
                s.brake_max_force,
                s.brake_curve,
                s.brake_full_force_at,
                s.pedal_value_max,
            )
        if s.enable_handbrake_bonus and t.get("handbrake", 0):
            force += s.handbrake_bonus
        return rigid(force)

    # --- Right trigger: throttle (priority chain) ----------------------------

    def _throttle(self, t, s, now):
        accel = t.get("accel", 0)
        gear = t.get("gear", 0)
        speed = t.get("speed", 0.0)

        # Detect upshifts and downshifts between valid gears -> arm shift burst.
        if (s.enable_gear_shift
                and self._prev_gear > 0
                and gear > 0
                and gear != self._prev_gear
                and speed > 3.0):
            self._shift_until = now + s.gear_shift_duration_ms / 1000.0
        self._prev_gear = gear

        # 1. Gear shift burst must win full-throttle too; FH5 shifts usually
        # happen while accel is pinned.
        if s.enable_gear_shift and now < self._shift_until:
            return vibration(s.gear_shift_freq, s.gear_shift_amp)

        # 2. Rev limiter
        rpm_r = self._ratio(t.get("rpm", 0.0), t.get("max_rpm", 0.0))
        if s.enable_rev_limiter and accel >= s.accel_deadzone and rpm_r > s.rev_limit_ratio:
            return vibration(s.rev_limit_freq, s.rev_limit_amp)

        if not s.enable_throttle_resistance:
            return off()

        # No throttle pressed -> hold baseline (no off<->rigid toggle jitter)
        if accel < s.accel_deadzone:
            return rigid(s.throttle_baseline_force)

        # 3. Progressive resistance (exponential: soft early, sharp late).
        return rigid(self._pedal_force(
            accel,
            s.accel_deadzone,
            s.throttle_baseline_force,
            s.throttle_max_force,
            s.throttle_curve,
            s.throttle_full_force_at,
            s.pedal_value_max,
        ))

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _abs_active(t, s, brake):
        if not s.enable_abs:
            return False
        if brake < s.abs_brake_threshold or t.get("speed", 0.0) < s.abs_min_speed_kmh:
            return False
        ratio = _max_abs(t, "tire_slip_ratio")
        combined = _max_abs(t, "tire_combined_slip")
        return ratio >= s.abs_slip_ratio_threshold or combined >= s.abs_combined_slip_threshold

    @staticmethod
    def _pedal_force(value, deadzone, baseline, max_force, curve, full_force_at, value_max):
        if value >= full_force_at:
            return RAW_MAX
        ratio = (value - deadzone) / max(value_max - deadzone, 1)
        return baseline + (max_force - baseline) * (ratio ** curve)

    @staticmethod
    def _ratio(value, max_value):
        if max_value <= 0:
            return 0.0
        return max(0.0, min(float(value) / float(max_value), 1.0))


def _max_abs(t, prefix):
    return max(abs(t.get(f"{prefix}_{wheel}", 0.0)) for wheel in ("fl", "fr", "rl", "rr"))

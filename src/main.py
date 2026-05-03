import argparse
import logging
import sys
import time

from modules import dualsense, udplistener, setup_logging
from modules.settings import Settings

log = logging.getLogger("fh5ds")


def run(s: Settings) -> None:
    ds = dualsense.DualSense(
        startup_pulse_force=s.startup_pulse_force,
        enable_startup_pulse=s.enable_startup_pulse,
    )
    ds.open()
    try:
        with udplistener.UDPListener(s.udp_host, s.udp_port, s.udp_timeout) as listener:
            log.info("Listening on %s:%d | Ctrl+C to quit", s.udp_host, s.udp_port)
            log.info("  In FH5: HUD & Gameplay -> Data Out: ON, IP 127.0.0.1, Port %d", s.udp_port)
            _loop(ds, listener, s)
    finally:
        ds.close()


def _loop(ds, listener, s):
    OFF = dualsense.triggers.off()
    anim = dualsense.TriggerAnimation()
    prev = None
    last_pkt = time.monotonic()
    last_log = 0.0
    pkt_count = 0

    while True:
        pkt, addr = listener.recv_latest()
        now = time.monotonic()

        if pkt is None:
            idle = now - last_pkt
            if idle > 5.0 and not getattr(listener, "lost", False):
                log.warning("No UDP packets yet — check FH5 Data Out IP/port and Windows Firewall")
                listener.lost = True
            if idle > 1.0 and prev != (OFF, OFF):
                ds.set(OFF, OFF); prev = (OFF, OFF)
            continue

        pkt_count += 1
        last_pkt = now
        listener.lost = False
        if pkt_count == 1:
            log.info("First packet from %s:%d (%d bytes)", addr[0], addr[1], len(pkt))

        try:
            t = udplistener.parse_packet(pkt)
        except ValueError as e:
            log.warning("Bad packet from %s:%d (%d bytes): %s", addr[0], addr[1], len(pkt), e)
            continue

        left, right = anim.update(t, s)

        if (left, right) != prev:
            ds.set(left, right); prev = (left, right)

        if now - last_log >= 1.0:
            last_log = now
            tag = "RACE" if t["on"] else "MENU"
            slip_r = _max_abs(t, "tire_slip_ratio")
            slip_c = _max_abs(t, "tire_combined_slip")
            log.debug("[%s] %6.1f km/h | gear %d | gas %3d R=%s | brake %3d L=%s | slip %.2f combined %.2f",
                      tag, t["speed"], t.get("gear", 0), t["accel"], right, t["brake"], left, slip_r, slip_c)


def _max_abs(t, prefix):
    return max(abs(t.get(f"{prefix}_{wheel}", 0.0)) for wheel in ("fl", "fr", "rl", "rr"))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="FH5 DualSense adaptive triggers (Steam keeps rumble)")
    p.add_argument("--host", default="127.0.0.1", help="UDP bind address (overrides Settings)")
    p.add_argument("--port", type=int, default=None, help="UDP port (overrides Settings)")
    p.add_argument("--debug", action="store_true", help="Verbose per-packet logs")
    args = p.parse_args()

    settings = Settings()
    if args.host is not None: settings.udp_host = args.host
    if args.port is not None: settings.udp_port = args.port

    setup_logging(args.debug)
    
    log.debug("Debug logging enabled")
    try:
        run(settings)
    except KeyboardInterrupt:
        sys.exit(0)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Capture the ESP32 console to a log file (needs pyserial; the ESP-IDF venv has it).

  serial_capture.py --port /dev/ttyUSB0|COM7 --out results/serial.log [--reset] [--seconds 600]

--reset pulses EN via RTS (DOIT DevKit V1 auto-reset circuit) so the log
starts at boot and the boot-timing metrics are captured.
Each line is prefixed with host time so it can be correlated with wg_bench.py.
"""
import argparse
import os
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing: run via `make log` / `.\tm log` (uses the ESP-IDF python) or pip install pyserial")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", default="results/serial.log")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = until Ctrl-C)")
    ap.add_argument("--quiet", action="store_true", help="only echo METRIC lines")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    ser = serial.Serial(a.port, a.baud, timeout=0.5)
    if a.reset:
        ser.dtr = False   # GPIO0 high -> normal boot
        ser.rts = True    # EN low
        time.sleep(0.1)
        ser.rts = False
    t_end = time.time() + a.seconds if a.seconds else None
    with open(a.out, "a", buffering=1, encoding="utf-8") as f:
        try:
            while t_end is None or time.time() < t_end:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").rstrip()
                stamped = f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} {line}"
                f.write(stamped + "\n")
                if not a.quiet or "METRIC" in line:
                    print(stamped)
        except KeyboardInterrupt:
            pass
    ser.close()


if __name__ == "__main__":
    main()

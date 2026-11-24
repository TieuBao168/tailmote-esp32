#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Phase 3 recovery test: how long the board is unreachable over the tailnet.

A UDP probe goes to the board's echo port (tailnet IP) every --interval s;
outages are the gaps between answered probes. Scenarios run in order:

  wifi:N      board drops its Wi-Fi for N s (test hook "WIFI_DOWN N",
              CONFIG_TM_TEST_HOOKS) and reconnects by itself
  peer:N      this PC runs `tailscale down`, waits N s, `tailscale up`

  ts_recovery.py --host 100.x.y.z --out results/phase3/recovery.jsonl wifi:30 wifi:120 peer:30

Standard library only. Prints one JSON line per scenario.
"""
import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time


class Prober(threading.Thread):
    def __init__(self, host, port, interval):
        super().__init__(daemon=True)
        self.host, self.port, self.interval = host, port, interval
        self.ok_times = []          # monotonic times of answered probes
        self.stop = False

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(self.interval)
        seq = 0
        while not self.stop:
            t0 = time.monotonic()
            seq += 1
            try:
                s.sendto(struct.pack("!Id", seq, t0) + b"p" * 20, (self.host, self.port))
                while True:
                    data, _ = s.recvfrom(2048)
                    if struct.unpack("!I", data[:4])[0] == seq:
                        self.ok_times.append(time.monotonic())
                        break
            except (socket.timeout, OSError):
                pass
            time.sleep(max(0.0, self.interval - (time.monotonic() - t0)))

    def last_ok_before(self, t):
        prev = [x for x in self.ok_times if x <= t]
        return prev[-1] if prev else None

    def first_ok_after(self, t):
        nxt = [x for x in self.ok_times if x > t]
        return nxt[0] if nxt else None


def send_cmd(host, port, msg, tries=10):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    for _ in range(tries):
        s.sendto(msg, (host, port))
        try:
            data, _ = s.recvfrom(64)
            if data == b"OK":
                return True
        except socket.timeout:
            continue
    return False


def tailscale_cli():
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        cand = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe")
        exe = cand if os.path.isfile(cand) else None
    return exe


def run_scenario(name, arg, a, prober):
    t_event = time.monotonic()
    t_back_cause = None
    if name == "wifi":
        if not send_cmd(a.host, a.port, b"WIFI_DOWN %d" % arg):
            return {"scenario": f"wifi:{arg}", "error": "board did not accept WIFI_DOWN"}
        t_back_cause = t_event + arg            # board reconnects after arg s
    elif name == "peer":
        ts = tailscale_cli()
        if not ts:
            return {"scenario": f"peer:{arg}", "error": "tailscale CLI not found"}
        r = subprocess.run([ts, "down"], capture_output=True, text=True)
        if r.returncode:
            return {"scenario": f"peer:{arg}", "error": f"tailscale down: {r.stderr.strip()[:120]}"}
        time.sleep(arg)
        r = subprocess.run([ts, "up"], capture_output=True, text=True)
        t_back_cause = time.monotonic()
        if r.returncode:
            return {"scenario": f"peer:{arg}", "error": f"tailscale up: {r.stderr.strip()[:120]}"}
    else:
        return {"scenario": f"{name}:{arg}", "error": "unknown scenario"}

    deadline = t_back_cause + a.timeout
    while time.monotonic() < deadline and prober.first_ok_after(t_back_cause) is None:
        time.sleep(0.5)
    last_ok = prober.last_ok_before(t_event)
    back = prober.first_ok_after(t_back_cause)
    res = {"scenario": f"{name}:{arg}",
           "outage_s": round(back - (last_ok or t_event), 1) if back else None,
           "recovered_after_cause_s": round(back - t_back_cause, 1) if back else None,
           "recovered": back is not None}
    time.sleep(a.settle)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="+", help="wifi:N | peer:N")
    ap.add_argument("--host", required=True, help="board tailnet IP")
    ap.add_argument("--port", type=int, default=7)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=300, help="give up this long after the cause ends")
    ap.add_argument("--settle", type=float, default=20, help="pause between scenarios")
    ap.add_argument("--out", help="append JSON results here")
    a = ap.parse_args()

    prober = Prober(a.host, a.port, a.interval)
    prober.start()
    # Opening the serial port for a log may reset the board: wait for it.
    t0 = time.monotonic()
    while not prober.ok_times and time.monotonic() - t0 < 90:
        time.sleep(0.5)
    time.sleep(5)
    if not prober.ok_times:
        sys.exit(f"error: no answer from {a.host}:{a.port} before the first scenario")
    results = []
    for sc in a.scenarios:
        name, _, arg = sc.partition(":")
        r = run_scenario(name, int(arg or 30), a, prober)
        r["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        print(json.dumps(r), flush=True)
        results.append(r)
    prober.stop = True
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "a") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
    if any("error" in r or not r.get("recovered") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()

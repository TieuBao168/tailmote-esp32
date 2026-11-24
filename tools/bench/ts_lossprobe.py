#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Per-packet loss probe: is packet loss in the tunnel or on the Wi-Fi?

Two probes at the same rate, logged per packet with wall-clock time:
  tun  UDP echo to the board's tailnet IP (WireGuard / DERP path)
  lan  UDP echo to the board's LAN IP, same port: answered by MicroLink only
       if the socket is bound to the tailnet IP, so by default `lan` uses ICMP
       ping (lwIP answers it directly, no tunnel involved).
Losses in both at the same time point at Wi-Fi; losses only in `tun` point at
the tunnel (rekey, path switch, DERP).

  ts_lossprobe.py --tun 100.x.y.z --lan 192.168.1.36 --seconds 900 --out results/phase3/loss.csv
"""
import argparse
import csv
import os
import socket
import struct
import subprocess
import sys
import threading
import time


def udp_probe(host, port, interval, end, rows, lock):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(min(1.0, interval))
    seq = 0
    while time.time() < end:
        t0 = time.time()
        seq += 1
        ok, rtt = 0, ""
        try:
            s.sendto(struct.pack("!Id", seq, t0) + b"L" * 20, (host, port))
            while True:
                data, _ = s.recvfrom(2048)
                if struct.unpack("!I", data[:4])[0] == seq:
                    ok, rtt = 1, round((time.time() - t0) * 1000, 1)
                    break
        except (socket.timeout, OSError):
            pass
        with lock:
            rows.append(("tun", seq, round(t0, 3), ok, rtt))
        time.sleep(max(0.0, interval - (time.time() - t0)))


def icmp_probe(host, interval, end, rows, lock):
    seq = 0
    win = os.name == "nt"
    while time.time() < end:
        t0 = time.time()
        seq += 1
        cmd = ["ping", "-n", "1", "-w", "1000", host] if win else ["ping", "-c", "1", "-W", "1", host]
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = r.stdout
        ok = int(r.returncode == 0 and ("TTL=" in out or "ttl=" in out))
        rtt = round((time.time() - t0) * 1000, 1) if ok else ""
        with lock:
            rows.append(("lan", seq, round(t0, 3), ok, rtt))
        time.sleep(max(0.0, interval - (time.time() - t0)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tun", required=True, help="board tailnet IP")
    ap.add_argument("--lan", required=True, help="board LAN IP")
    ap.add_argument("--port", type=int, default=7)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--seconds", type=float, default=900)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rows, lock = [], threading.Lock()
    end = time.time() + a.seconds
    th = [threading.Thread(target=udp_probe, args=(a.tun, a.port, a.interval, end, rows, lock)),
          threading.Thread(target=icmp_probe, args=(a.lan, a.interval, end, rows, lock))]
    for t in th:
        t.start()
    for t in th:
        t.join()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["probe", "seq", "t_unix", "ok", "rtt_ms"])
        w.writerows(sorted(rows, key=lambda r: r[2]))
    for p in ("tun", "lan"):
        r = [x for x in rows if x[0] == p]
        lost = sum(1 for x in r if not x[3])
        print(f"{p}: {len(r)} sent, {lost} lost ({100 * lost / max(1, len(r)):.1f} %)")


if __name__ == "__main__":
    sys.exit(main())

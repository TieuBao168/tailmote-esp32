#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Phase 2 benchmark client (runs on the WireGuard peer: Linux, macOS or Windows).

Talks to the bench services in firmware/phase2-wg-poc (see main/bench.h).
Point --host at the tunnel IP (10.66.0.2) to measure WireGuard, or at the
ESP32's LAN IP to get a no-tunnel baseline with the same firmware.

  wg_bench.py rtt     --count 200 --size 64
  wg_bench.py udp-rx  --rate 2 --duration 10 --size 1200   # Mbit/s offered
  wg_bench.py tcp-rx  --duration 10                         # Linux -> ESP32
  wg_bench.py tcp-tx                                        # ESP32 -> Linux
  wg_bench.py all     --out results/run.json

Standard library only.
"""
import argparse
import json
import os
import socket
import statistics
import struct
import sys
import time

UDP_ECHO, UDP_SINK, TCP_SINK, TCP_SRC = 7, 5001, 5002, 5003


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[k]


def rtt(host, count, size, interval, timeout):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    pad = b"x" * max(0, size - 12)
    rtts, lost = [], 0
    for seq in range(count):
        t0 = time.perf_counter()
        s.sendto(struct.pack("!Id", seq, t0) + pad, (host, UDP_ECHO))
        try:
            while True:
                data, _ = s.recvfrom(2048)
                rseq, _ = struct.unpack("!Id", data[:12])
                if rseq == seq:
                    rtts.append((time.perf_counter() - t0) * 1000)
                    break
        except socket.timeout:
            lost += 1
        time.sleep(interval)
    s.close()
    return {
        "test": "rtt", "count": count, "size": size, "lost": lost,
        "loss_pct": round(100 * lost / count, 2),
        "rtt_min_ms": round(min(rtts), 2) if rtts else None,
        "rtt_avg_ms": round(statistics.mean(rtts), 2) if rtts else None,
        "rtt_p50_ms": round(pct(rtts, 50), 2) if rtts else None,
        "rtt_p95_ms": round(pct(rtts, 95), 2) if rtts else None,
        "rtt_max_ms": round(max(rtts), 2) if rtts else None,
        "jitter_ms": round(statistics.pstdev(rtts), 2) if len(rtts) > 1 else None,
    }


def udp_ctrl(s, host, msg, timeout=2.0, port=None):
    port = port or UDP_SINK
    s.settimeout(timeout)
    for _ in range(5):
        s.sendto(msg, (host, port))
        try:
            data, _ = s.recvfrom(2048)
            return data
        except socket.timeout:
            continue
    raise RuntimeError(f"no reply to {msg!r} from {host}:{port}")


def udp_rx(host, rate_mbit, duration, size, port=None, prefix=b""):
    """port/prefix: the Phase 3 firmware multiplexes the sink on its echo port
    (each MicroLink socket costs ~11 KB RAM) and wants b"SINK" before the seq."""
    port = port or UDP_SINK
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_ctrl(s, host, b"RST", port=port)
    pps = rate_mbit * 1e6 / 8 / size
    gap = 1.0 / pps
    pad = b"u" * (size - 4 - len(prefix))
    sent, t_start = 0, time.perf_counter()
    next_t = t_start
    while time.perf_counter() - t_start < duration:
        s.sendto(prefix + struct.pack("!I", sent + 1) + pad, (host, port))
        sent += 1
        next_t += gap
        delay = next_t - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
    send_s = time.perf_counter() - t_start
    time.sleep(0.5)
    st = json.loads(udp_ctrl(s, host, b"STAT", port=port))
    s.close()
    dur = st["dur_ms"] / 1000 if st["dur_ms"] else send_s
    return {
        "test": "udp-rx", "offered_mbit": rate_mbit, "size": size, "sent": sent,
        "received": st["pkts"], "loss_pct": round(100 * (sent - st["pkts"]) / sent, 2) if sent else None,
        "reorder": st["reorder"],
        "goodput_mbit": round(st["bytes"] * 8 / dur / 1e6, 3) if dur else None,
    }


def tcp_rx(host, duration):
    s = socket.create_connection((host, TCP_SINK), timeout=10)
    buf = os.urandom(64 * 1024)
    sent, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < duration:
        sent += s.send(buf)
    s.shutdown(socket.SHUT_WR)
    s.recv(1)  # wait for ESP32 to close -> all data consumed
    dt = time.perf_counter() - t0
    s.close()
    return {"test": "tcp-rx", "bytes": sent, "seconds": round(dt, 2), "mbit": round(sent * 8 / dt / 1e6, 3)}


def tcp_tx(host):
    s = socket.create_connection((host, TCP_SRC), timeout=10)
    got, t0 = 0, time.perf_counter()
    while True:
        d = s.recv(65536)
        if not d:
            break
        got += len(d)
    dt = time.perf_counter() - t0
    s.close()
    return {"test": "tcp-tx", "bytes": got, "seconds": round(dt, 2), "mbit": round(got * 8 / dt / 1e6, 3)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["rtt", "udp-rx", "tcp-rx", "tcp-tx", "all"])
    ap.add_argument("--host", default="10.66.0.2")
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--size", type=int, default=None, help="payload bytes")
    ap.add_argument("--interval", type=float, default=0.05)
    ap.add_argument("--timeout", type=float, default=1.0)
    ap.add_argument("--rate", type=float, default=2.0, help="UDP offered rate, Mbit/s")
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--label", default="", help="free text stored with results (e.g. 'tunnel, PS none')")
    ap.add_argument("--out", help="append JSON results to this file (one object per line)")
    ap.add_argument("--sink-port", type=int, default=None, help="UDP sink port (default 5001; Phase 3: 7)")
    ap.add_argument("--sink-prefix", default="", help="bytes before the sequence number (Phase 3: SINK)")
    ap.add_argument("--rates", default="1,2,4,8", help="udp-rx rates for mode all, Mbit/s")
    a = ap.parse_args()

    results = []

    def attempt(test, fn, *args, **extra):
        # One failing test (lost control packet, refused connection) must not
        # discard the results already measured: record the error and go on.
        try:
            results.append(fn(*args))
        except (OSError, RuntimeError, ValueError) as e:
            print(f"warning: {test} failed: {e}", file=sys.stderr)
            results.append({"test": test, **extra, "error": str(e)})

    if a.mode in ("rtt", "all"):
        attempt("rtt", rtt, a.host, a.count, a.size or 64, a.interval, a.timeout)
        if a.mode == "all":
            attempt("rtt", rtt, a.host, a.count, 1200, a.interval, a.timeout)
    if a.mode in ("udp-rx", "all"):
        rates = [a.rate] if a.mode == "udp-rx" else [float(x) for x in a.rates.split(",")]
        for r in rates:
            attempt("udp-rx", udp_rx, a.host, r, a.duration, a.size or 1200, a.sink_port,
                    a.sink_prefix.encode(), offered_mbit=r)
            time.sleep(1)
    if a.mode in ("tcp-rx", "all"):
        attempt("tcp-rx", tcp_rx, a.host, a.duration)
        time.sleep(1)
    if a.mode in ("tcp-tx", "all"):
        attempt("tcp-tx", tcp_tx, a.host)

    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for r in results:
        r.update({"host": a.host, "label": a.label, "time": stamp})
        print(json.dumps(r))
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "a") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
    failed = sum("error" in r for r in results)
    if failed:
        sys.exit(f"{failed}/{len(results)} test(s) failed — partial results above")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as e:
        sys.exit(f"error: {e}")

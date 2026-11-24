#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Summarise METRIC lines from a serial log captured with serial_capture.py.

  parse_metrics.py results/serial.log [--csv results/periodic.csv] [--json]

Prints: boot timeline, RAM cost per block, heap / CPU over time,
task stack minimums, reconnect statistics and on-device throughput.
"""
import argparse
import csv
import json
import re
import statistics
import sys

RE = re.compile(r"METRIC (\{.*\})")


def load(path):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = RE.search(line)
            if not m:
                continue
            try:
                out.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                pass
    return out


def summarise(ev):
    s = {}
    boots = [e for e in ev if e["ev"] == "boot"]
    if boots:
        s["chip"] = {k: boots[-1].get(k) for k in ("model", "rev", "cores", "flash", "idf", "reset", "app")}
        s["boots"] = len(boots)

    # Timeline of the last boot
    last_boot = max((i for i, e in enumerate(ev) if e["ev"] == "boot"), default=0)
    tl = {}
    for e in ev[last_boot:]:
        if e["ev"] in ("base_init", "wifi_got_ip", "sntp_synced", "wg_up") and e["ev"] not in tl:
            tl[e["ev"]] = e["t_ms"]
            if e["ev"] == "wg_up":
                tl["wg_handshake_ms"] = e.get("handshake_ms")
    s["boot_timeline_ms"] = tl

    s["ram_cost_bytes"] = {}
    for e in ev[last_boot:]:
        if e["ev"] == "heap_delta":
            s["ram_cost_bytes"].setdefault(e["what"], e["used"])

    per = [e for e in ev if e["ev"] == "periodic"]
    if per:
        cpu0 = [e["cpu0"] for e in per if e["cpu0"] >= 0]
        cpu1 = [e["cpu1"] for e in per if e["cpu1"] >= 0]
        s["heap"] = {
            "free_min": min(e["heap"] for e in per),
            "free_last": per[-1]["heap"],
            "largest_block_min": min(e["largest"] for e in per),
            "watermark_min": min(e["heap_min"] for e in per),
            "samples": len(per),
            "uptime_s": round(per[-1]["t_ms"] / 1000),
        }
        s["cpu_pct"] = {
            "core0_avg": round(statistics.mean(cpu0), 1) if cpu0 else None,
            "core0_max": max(cpu0) if cpu0 else None,
            "core1_avg": round(statistics.mean(cpu1), 1) if cpu1 else None,
            "core1_max": max(cpu1) if cpu1 else None,
        }

    stacks = {}
    for e in ev:
        if e["ev"] == "task":
            stacks[e["name"]] = min(stacks.get(e["name"], 1 << 30), e["stack_free_min"])
    if stacks:
        s["task_stack_free_min_bytes"] = dict(sorted(stacks.items(), key=lambda kv: kv[1]))

    ups = [e for e in ev if e["ev"] == "wg_up"]
    s["tunnel"] = {
        "sessions": len(ups),
        "handshake_ms": [e["handshake_ms"] for e in ups],
        "down_events": [e.get("why") for e in ev if e["ev"] == "wg_down"],
        "wifi_disconnects": sum(1 for e in ev if e["ev"] == "wifi_disconnected"),
    }

    # Recovery time: wifi_got_ip after a disconnect -> next wg_up
    rec = []
    pending = None
    for e in ev:
        if e["ev"] == "wifi_disconnected" and pending is None:
            pending = e["t_ms"]
        elif e["ev"] == "wg_up" and pending is not None:
            rec.append(e["t_ms"] - pending)
            pending = None
        elif e["ev"] == "boot":
            pending = None
    if rec:
        s["tunnel"]["recovery_ms"] = rec

    for kind in ("tcp_sink", "tcp_source", "udp_sink"):
        xs = [e for e in ev if e["ev"] == kind]
        if xs:
            s[kind] = xs
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--csv", help="write periodic samples to CSV")
    ap.add_argument("--json", action="store_true", help="print raw JSON summary")
    a = ap.parse_args()

    ev = load(a.log)
    if not ev:
        sys.exit("no METRIC lines found")
    s = summarise(ev)

    if a.csv:
        per = [e for e in ev if e["ev"] == "periodic"]
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["t_ms", "heap", "largest", "heap_min", "cpu0", "cpu1", "tasks"])
            w.writeheader()
            for e in per:
                w.writerow({k: e.get(k) for k in w.fieldnames})

    print(json.dumps(s, indent=None if a.json else 2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Long-running stability (soak) test for the Phase 3 board.

  soak.py capture --port /dev/serial/by-id/... [--dir results/soak]
  soak.py report  [--dir results/soak | LOG ...] [--out results/soak/report.md]

capture keeps the serial port open (exclusively, so no other terminal can
steal or corrupt the stream), writes every line with a full ISO timestamp to
one file per day (soak-YYYYMMDD.log) and reopens the port by itself after a
USB unplug or read error. On this board opening the port resets the chip, so
each (re)open is logged as a "# capture:" marker and the boot that follows is
not counted as a crash.

report reads those logs and grades the soak cases (see CASES below) as
PASS / WARN / FAIL from the board's METRIC telemetry. It only needs the
standard library; capture needs pyserial (the ESP-IDF python has it).
"""
import argparse
import glob
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta

MARK = "# capture:"
RE_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}) (.*)$")
RE_METRIC = re.compile(r"METRIC (\{.*\})")
RE_LOG = re.compile(r"^(?:\x1b\[[0-9;]*m)?([WE]) \((\d+)\) ([\w.-]+): (.*?)(?:\x1b\[0m)?$")
CRASH_MARKERS = ("Guru Meditation", "abort() was called", "Backtrace:", "Brownout detector",
                 "Task watchdog got triggered", "stack overflow", "assert failed")

# esp_reset_reason_t
RESET_REASONS = {0: "UNKNOWN", 1: "POWERON", 2: "EXT", 3: "SW", 4: "PANIC", 5: "INT_WDT",
                 6: "TASK_WDT", 7: "WDT", 8: "DEEPSLEEP", 9: "BROWNOUT", 10: "SDIO"}

CASES = {
    "S1": "No unexpected reboot or crash",
    "S2": "Heap: no leak, no failed allocation, enough headroom",
    "S3": "Tailnet availability and recovery after outages",
    "S4": "Wi-Fi: disconnects recover",
    "S5": "Task stacks keep a safety margin",
    "S6": "CPU load stays low when idle",
    "S7": "Exit-node / tunnel traffic seen by the packet filter",
}


# ------------------------------------------------------------------ capture --
def _ts():
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"


def capture(a):
    try:
        import serial
    except ImportError:
        sys.exit("pyserial missing: run with the ESP-IDF python (tm soak does this)")
    os.makedirs(a.dir, exist_ok=True)
    # systemctl stop sends SIGTERM: end like Ctrl-C so the "stopped" marker is written.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    t_end = time.time() + a.seconds if a.seconds else None
    ser = None
    day = out = None

    def write(text):
        nonlocal day, out
        today = time.strftime("%Y%m%d")
        if today != day:
            if out:
                out.close()
            day = today
            out = open(os.path.join(a.dir, f"soak-{day}.log"), "a", buffering=1, encoding="utf-8")
        out.write(f"{_ts()} {text}\n")

    try:
        while t_end is None or time.time() < t_end:
            if ser is None:
                try:
                    ser = serial.Serial(a.port, a.baud, timeout=1, exclusive=True)
                    # Opening the port does not always reset the chip; pulse EN
                    # (RTS, DOIT DevKit V1 auto-reset circuit) so every capture
                    # starts at boot and the tailnet state is known.
                    ser.dtr = False
                    ser.rts = True
                    time.sleep(0.1)
                    ser.rts = False
                    write(f"{MARK} opened {a.port} (board resets)")
                    print(f"{_ts()} capturing {a.port} -> {a.dir}", flush=True)
                except (serial.SerialException, OSError) as e:
                    write(f"{MARK} cannot open {a.port}: {e}; retrying in 10 s")
                    time.sleep(10)
                    continue
            try:
                raw = ser.readline()
            except (serial.SerialException, OSError) as e:
                write(f"{MARK} read error: {e}; reopening")
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                time.sleep(5)
                continue
            if raw:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                write(line)
                if a.echo and "METRIC" not in line:
                    print(line, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        write(f"{MARK} stopped")
        if ser:
            ser.close()
        if out:
            out.close()


# ------------------------------------------------------------------- report --
def load(paths):
    """[(datetime, kind, payload)] with kind = 'mark' | 'metric' | 'log' | 'crash'."""
    rows = []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                m = RE_LINE.match(raw.rstrip("\n"))
                if not m:
                    continue
                ts = datetime.fromisoformat(m.group(1))
                body = m.group(2)
                if body.startswith(MARK):
                    rows.append((ts, "mark", body[len(MARK):].strip()))
                    continue
                mm = RE_METRIC.search(body)
                if mm:
                    try:
                        rows.append((ts, "metric", json.loads(mm.group(1))))
                    except json.JSONDecodeError:
                        pass
                    continue
                lm = RE_LOG.match(body)
                if lm:
                    rows.append((ts, "log", {"level": lm.group(1), "tag": lm.group(3), "msg": lm.group(4)}))
                    continue
                if any(c in body for c in CRASH_MARKERS):
                    rows.append((ts, "crash", body.strip()))
    rows.sort(key=lambda r: r[0])
    return rows


def _slope_per_hour(points):
    """Least-squares slope of (datetime, value) in value/hour, or None."""
    if len(points) < 10:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 3600 for t, _ in points]
    ys = [v for _, v in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var


def _fmt_dur(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def analyse(rows, th):
    if not rows:
        return None
    start, end = rows[0][0], rows[-1][0]
    r = {"start": start, "end": end, "span_s": (end - start).total_seconds(), "cases": {}}

    # --- reboots: a boot right after a capture (re)open is expected
    boots, crashes, last_mark = [], [r_[2] for r_ in rows if r_[1] == "crash"], None
    for ts, kind, p in rows:
        if kind == "mark" and p.startswith("opened"):
            last_mark = ts
        elif kind == "metric" and p.get("ev") == "boot":
            expected = last_mark is not None and (ts - last_mark) < timedelta(seconds=5)
            boots.append({"ts": ts, "reset": RESET_REASONS.get(p.get("reset"), p.get("reset")),
                          "app": p.get("app"), "expected": expected})
            last_mark = None
    unexpected = [b for b in boots if not b["expected"]]
    r["boots"], r["app"] = boots, (boots[-1]["app"] if boots else None)
    s1 = "FAIL" if unexpected or crashes else "PASS"
    r["cases"]["S1"] = (s1, f"{len(boots)} boot(s), {len(unexpected)} unexpected"
                        + (f" ({', '.join(b['reset'] + ' at ' + b['ts'].strftime('%m-%d %H:%M') for b in unexpected)})"
                           if unexpected else "") + f", {len(crashes)} crash marker(s)")

    # --- heap (per boot segment, after warm-up)
    per = [(ts, p) for ts, kind, p in rows if kind == "metric" and p.get("ev") == "periodic"]
    allocs = [p for _, kind, p in rows if kind == "metric" and p.get("ev") == "alloc_failed"]
    seg, segs = [], []
    for ts, p in per:
        if seg and p["t_ms"] < seg[-1][1]["t_ms"]:
            segs.append(seg)
            seg = []
        seg.append((ts, p))
    if seg:
        segs.append(seg)
    slopes = []
    for sg in segs:
        warm = [(ts, p["heap"]) for ts, p in sg if p["t_ms"] >= th.warmup_s * 1000]
        sl = _slope_per_hour(warm)
        if sl is not None and (warm[-1][0] - warm[0][0]).total_seconds() >= 1800:
            slopes.append(sl)
    if per:
        heap_min = min(p["heap_min"] for _, p in per)
        largest_min = min(p["largest"] for _, p in per)
        free_last = per[-1][1]["heap"]
        r["heap"] = {"watermark_min": heap_min, "largest_min": largest_min, "free_last": free_last,
                     "slope_b_per_h": round(slopes[-1]) if slopes else None, "samples": len(per)}
        status = "PASS"
        notes = [f"watermark min {heap_min} B, largest block min {largest_min} B, free now {free_last} B"]
        if allocs:
            status = "FAIL"
            notes.append(f"{len(allocs)} failed allocation(s)")
        if heap_min < th.heap_min:
            status = "FAIL"
            notes.append(f"watermark below {th.heap_min} B")
        if slopes:
            notes.append(f"trend {slopes[-1]:+.0f} B/h")
            if slopes[-1] < -th.leak_b_per_h:
                status = "FAIL" if status != "FAIL" else status
                notes.append(f"leak suspected (< -{th.leak_b_per_h} B/h)")
        else:
            notes.append("trend needs >= 30 min after warm-up")
            if status == "PASS":
                status = "WARN"
        r["cases"]["S2"] = (status, "; ".join(notes))
    else:
        r["cases"]["S2"] = ("WARN", "no periodic samples yet")

    # --- tailnet availability from ml_state (wall clock, across reboots).
    # The time from a capture (re)open, which resets the board on purpose, to
    # the next CONNECTED is excluded: it is the test harness, not the board.
    connected_since = down_since = paused_since = first_up = None
    up_s = excluded_s = 0.0
    outages = []
    prev_ts = start
    for ts, kind, p in rows:
        last_seen, prev_ts = prev_ts, ts
        is_open = kind == "mark" and p.startswith("opened")
        is_boot = kind == "metric" and p.get("ev") == "boot"
        if is_open or is_boot:
            # The board went silent before this (re)boot: the outage starts at
            # the last line it printed, not at the boot itself.
            ts = last_seen
        if kind == "metric" and p.get("ev") == "ml_state":
            if p.get("state") == "CONNECTED":
                if first_up is None:
                    first_up = ts
                if paused_since is not None:
                    excluded_s += (ts - paused_since).total_seconds()
                    paused_since = None
                if down_since is not None:
                    outages.append((down_since, (ts - down_since).total_seconds()))
                    down_since = None
                if connected_since is None:
                    connected_since = ts
            elif connected_since is not None:
                up_s += (ts - connected_since).total_seconds()
                connected_since, down_since = None, ts
        elif is_open:
            if connected_since is not None:
                up_s += (ts - connected_since).total_seconds()
            elif down_since is not None:           # already down: close that outage here
                outages.append((down_since, (ts - down_since).total_seconds()))
            connected_since = down_since = None
            if first_up is not None:
                paused_since = ts
        elif is_boot and paused_since is None:     # reboot not caused by the capture
            if connected_since is not None:
                up_s += (ts - connected_since).total_seconds()
                connected_since, down_since = None, ts
            elif down_since is None and first_up is not None:
                down_since = ts
    if connected_since is not None:
        up_s += (end - connected_since).total_seconds()
    if paused_since is not None:
        excluded_s += (end - paused_since).total_seconds()
    if first_up:
        span = (end - first_up).total_seconds() - excluded_s
        avail = 100.0 * up_s / span if span > 0 else 100.0
        longest = max((d for _, d in outages), default=0)
        r["tailnet"] = {"availability_pct": round(avail, 3), "outages": len(outages), "longest_s": longest}
        status = "PASS" if avail >= th.availability and longest <= th.max_outage_s else "FAIL"
        if down_since is not None and status == "PASS":
            status = "WARN"
        r["cases"]["S3"] = (status, f"available {avail:.2f}% over {_fmt_dur(span)}, {len(outages)} outage(s), "
                            f"longest {_fmt_dur(longest)}" + (" — currently DOWN" if down_since else ""))
    elif not boots and per:
        # A --since window that starts after boot: the last state change is
        # outside it, so availability cannot be graded here.
        r["cases"]["S3"] = ("WARN", "no ml_state change in this window (it starts after boot); not graded")
    else:
        r["cases"]["S3"] = ("FAIL" if r["span_s"] > 120 else "WARN", "never reached CONNECTED")

    # --- Wi-Fi
    disc = [(ts, p) for ts, kind, p in rows if kind == "metric" and p.get("ev") == "wifi_disconnected"]
    recov, pending = [], None
    for ts, kind, p in rows:
        if kind == "metric" and p.get("ev") == "wifi_disconnected" and pending is None:
            pending = ts
        elif kind == "metric" and p.get("ev") == "wifi_got_ip" and pending is not None:
            recov.append((ts - pending).total_seconds())
            pending = None
    rssi = [p["rssi"] for _, kind, p in rows if kind == "metric" and p.get("ev") == "wifi_got_ip" and "rssi" in p]
    reasons = sorted({p.get("reason") for _, p in disc})
    status = "PASS" if pending is None and all(x <= th.max_outage_s for x in recov) else "FAIL"
    r["cases"]["S4"] = (status, f"{len(disc)} disconnect(s)" + (f" (reasons {reasons})" if reasons else "")
                        + (f", recovered in max {_fmt_dur(max(recov))}" if recov else "")
                        + (f", RSSI at connect {min(rssi)}..{max(rssi)} dBm" if rssi else "")
                        + (" — still disconnected" if pending else ""))

    # --- stacks
    stacks = {}
    for _, kind, p in rows:
        if kind == "metric" and p.get("ev") == "task":
            stacks[p["name"]] = min(stacks.get(p["name"], 1 << 30), p["stack_free_min"])
    if stacks:
        low = {k: v for k, v in stacks.items() if v < th.stack_min}
        tight = sorted(stacks.items(), key=lambda kv: kv[1])[:4]
        r["stacks"] = stacks
        r["cases"]["S5"] = ("FAIL" if low else "PASS",
                            "lowest: " + ", ".join(f"{k} {v} B" for k, v in tight)
                            + (f"; below {th.stack_min} B: {', '.join(low)}" if low else ""))
    else:
        r["cases"]["S5"] = ("WARN", "no task samples yet")

    # --- CPU
    cpu = [(p["cpu0"], p["cpu1"]) for _, p in per if p.get("cpu0", -1) >= 0]
    if cpu:
        c0 = sorted(c for c, _ in cpu)
        med0 = c0[len(c0) // 2]
        r["cases"]["S6"] = ("PASS" if med0 <= th.cpu_idle else "WARN",
                            f"core0 median {med0:.1f}% max {max(c0):.1f}%, "
                            f"core1 max {max(c for _, c in cpu):.1f}%")
    else:
        r["cases"]["S6"] = ("WARN", "no CPU samples yet")

    # --- packet filter (exit-node traffic indicator; counters reset on boot)
    flt = [p for _, kind, p in rows if kind == "metric" and p.get("ev") == "filter"]
    acc = drop = 0
    prev = None
    for p in flt:
        if prev and p["accepted"] >= prev["accepted"]:
            acc += p["accepted"] - prev["accepted"]
            drop += max(0, p["dropped"] - prev["dropped"])
        prev = p
    r["cases"]["S7"] = ("PASS" if flt and flt[-1].get("configured") else "WARN",
                        f"{acc} packet(s) accepted, {drop} dropped by the filter"
                        + (f", {flt[-1].get('rules')} rule(s)" if flt else ", no filter samples"))

    # --- warnings / errors by tag
    logs = {}
    for _, kind, p in rows:
        if kind == "log":
            key = f"{p['level']} {p['tag']}: {re.sub(r'[0-9]+', '#', p['msg'])[:70]}"
            logs[key] = logs.get(key, 0) + 1
    r["logs"] = sorted(logs.items(), key=lambda kv: -kv[1])
    r["outages"] = outages
    return r


def timeline(rows, step_s=60):
    """Per-interval board load: [(start, cpu0_max, cpu1_max, heap_min_free, largest_min,
    accepted_per_s, alloc_failed)]. accepted_per_s counts tunnel packets the
    packet filter let in (phone -> board, exit-node traffic included)."""
    buckets = {}
    prev = None
    for ts, kind, p in rows:
        if kind != "metric":
            continue
        key = ts.replace(second=0, microsecond=0) if step_s == 60 else \
            datetime.fromtimestamp(int(ts.timestamp()) // step_s * step_s)
        b = buckets.setdefault(key, {"cpu0": 0.0, "cpu1": 0.0, "heap": None, "largest": None,
                                     "acc": 0, "alloc": 0})
        ev = p.get("ev")
        if ev == "periodic":
            b["cpu0"] = max(b["cpu0"], p.get("cpu0", 0))
            b["cpu1"] = max(b["cpu1"], p.get("cpu1", 0))
            b["heap"] = p["heap"] if b["heap"] is None else min(b["heap"], p["heap"])
            b["largest"] = p["largest"] if b["largest"] is None else min(b["largest"], p["largest"])
        elif ev == "filter":
            if prev is not None and p["accepted"] >= prev:
                b["acc"] += p["accepted"] - prev
            prev = p["accepted"]
        elif ev == "boot":
            prev = None
        elif ev == "alloc_failed":
            b["alloc"] += 1
    return [(k, b["cpu0"], b["cpu1"], b["heap"], b["largest"], b["acc"] / step_s, b["alloc"])
            for k, b in sorted(buckets.items())]


def render(r):
    if r is None:
        return "No soak data yet.\n"
    order = {"FAIL": 0, "WARN": 1, "PASS": 2}
    overall = min((c[0] for c in r["cases"].values()), key=lambda s: order[s])
    out = [f"# Soak report — {overall}", "",
           f"- Window: {r['start']:%Y-%m-%d %H:%M} → {r['end']:%Y-%m-%d %H:%M} ({_fmt_dur(r['span_s'])})",
           f"- Firmware: `{r.get('app') or 'unknown (no boot in this window)'}`", "", "| Case | Result | Details |", "|---|---|---|"]
    for cid in sorted(r["cases"]):
        st, det = r["cases"][cid]
        out.append(f"| {cid} {CASES[cid]} | **{st}** | {det} |")
    if r.get("timeline"):
        out += ["", "## Timeline (per minute)", "",
                "| Time | CPU0 max % | CPU1 max % | Heap free min | Largest block min | Tunnel pkts/s | alloc_failed |",
                "|---|---|---|---|---|---|---|"]
        out += [f"| {k:%H:%M} | {c0:.1f} | {c1:.1f} | {h if h is not None else '-'} | "
                f"{lg if lg is not None else '-'} | {acc:.1f} | {al} |"
                for k, c0, c1, h, lg, acc, al in r["timeline"]]
    if r["outages"]:
        out += ["", "## Tailnet outages", "", "| Start | Duration |", "|---|---|"]
        out += [f"| {t:%m-%d %H:%M:%S} | {_fmt_dur(d)} |" for t, d in r["outages"][-20:]]
    if r["logs"]:
        out += ["", "## Warnings / errors (by message, digits as #)", "", "| Count | Line |", "|---|---|"]
        out += [f"| {n} | `{k}` |" for k, n in r["logs"][:15]]
    return "\n".join(out) + "\n"


def _when(text):
    """'HH:MM' (today) or an ISO date-time, or None."""
    if not text:
        return None
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
        parts = [int(x) for x in text.split(":")] + [0]
        return datetime.now().replace(hour=parts[0], minute=parts[1], second=parts[2], microsecond=0)
    return datetime.fromisoformat(text)


def report(a):
    paths = a.logs or sorted(glob.glob(os.path.join(a.dir, "soak-*.log")))
    if not paths:
        sys.exit(f"no soak logs in {a.dir}")
    rows = load(paths)
    since, until = _when(a.since), _when(a.until)
    if since or until:
        rows = [row for row in rows if (not since or row[0] >= since) and (not until or row[0] <= until)]
    r = analyse(rows, a)
    if r is not None and a.timeline:
        r["timeline"] = timeline(rows)
    text = render(r)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text, end="")
    return 1 if text.startswith("# Soak report — FAIL") else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="record the console continuously")
    c.add_argument("--port", required=True)
    c.add_argument("--baud", type=int, default=115200)
    c.add_argument("--dir", default="results/soak")
    c.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = run until stopped)")
    c.add_argument("--echo", action="store_true", help="also print non-METRIC lines")
    r = sub.add_parser("report", help="grade the soak cases from the logs")
    r.add_argument("logs", nargs="*", help="log files (default: all soak-*.log in --dir)")
    r.add_argument("--dir", default="results/soak")
    r.add_argument("--out", help="also write the Markdown report here")
    r.add_argument("--since", help="only lines from this time: HH:MM (today) or ISO date-time")
    r.add_argument("--until", help="only lines up to this time: HH:MM (today) or ISO date-time")
    r.add_argument("--timeline", action="store_true", help="add a per-minute load table")
    r.add_argument("--heap-min", type=int, default=10240, help="min heap watermark, bytes")
    r.add_argument("--leak-b-per-h", type=int, default=512, help="max heap decline, bytes/hour")
    r.add_argument("--warmup-s", type=int, default=300, help="ignore heap trend this long after boot")
    r.add_argument("--availability", type=float, default=99.0, help="min %% time CONNECTED")
    r.add_argument("--max-outage-s", type=float, default=120, help="max single outage, seconds")
    r.add_argument("--stack-min", type=int, default=256, help="min free stack per task, bytes")
    r.add_argument("--cpu-idle", type=float, default=10.0, help="max median core0 load, %%")
    a = ap.parse_args(argv)
    if a.cmd == "capture":
        capture(a)
        return 0
    return report(a)


if __name__ == "__main__":
    sys.exit(main())

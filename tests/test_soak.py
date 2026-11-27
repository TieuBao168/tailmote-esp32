# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Unit tests for tools/bench/soak.py report (synthetic logs, no board needed)."""
import argparse
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "soak", Path(__file__).resolve().parent.parent / "tools" / "bench" / "soak.py")
soak = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(soak)

T0 = datetime(2026, 11, 27, 10, 0, 0)


def thresholds(**kw):
    d = dict(heap_min=10240, leak_b_per_h=512, warmup_s=300, availability=99.0,
             max_outage_s=120, stack_min=256, cpu_idle=10.0)
    d.update(kw)
    return argparse.Namespace(**d)


class Log:
    """Builds a soak log: lines stamped with wall-clock offsets from T0."""

    def __init__(self):
        self.lines = []
        self.boot_t = 0          # wall second of the current boot

    def at(self, sec, body):
        ts = (T0 + timedelta(seconds=sec)).isoformat(timespec="milliseconds")
        self.lines.append(f"{ts} {body}")

    def metric(self, sec, ev, **kv):
        kv = dict({"ev": ev, "t_ms": int((sec - self.boot_t) * 1000)}, **kv)
        self.at(sec, "METRIC " + json.dumps(kv))

    def boot(self, sec, reset=1, opened=True):
        if opened:
            self.at(sec, f"{soak.MARK} opened /dev/ttyUSB1 (board resets)")
        self.boot_t = sec
        self.metric(sec, "boot", reset=reset, app="test")

    def run(self, start, end, heap=60000, drift_per_h=0, state_at=12, step=10):
        self.metric(start + state_at, "ml_state", state="CONNECTED")
        for s in range(start + step, end, step):
            h = int(heap + drift_per_h * (s - start) / 3600)
            self.metric(s, "periodic", heap=h, largest=24576, heap_min=30000, cpu0=2.5, cpu1=1.0)
            self.metric(s, "filter", accepted=(s - start) // 10, dropped=0, rules=2, configured=1)
            if (s - start) % 60 == 0:
                self.metric(s, "task", name="ml_coord", stack_free_min=2064, core=1)

    def rows(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        return soak.load([f.name])


class SoakReport(unittest.TestCase):
    def test_clean_soak_passes(self):
        log = Log()
        log.boot(0)
        log.run(0, 4 * 3600)
        r = soak.analyse(log.rows(), thresholds())
        for cid in ("S1", "S2", "S3", "S4", "S5", "S6", "S7"):
            self.assertEqual(r["cases"][cid][0], "PASS", (cid, r["cases"][cid]))
        self.assertIn("# Soak report — PASS", soak.render(r))

    def test_unexpected_reboot_fails_s1_and_counts_outage(self):
        log = Log()
        log.boot(0)
        log.run(0, 3600)
        log.at(3600, "Guru Meditation Error: Core  0 panic'ed (LoadProhibited).")
        log.boot(3605, reset=4, opened=False)       # panic reboot, not caused by capture
        log.run(3605, 7200)
        r = soak.analyse(log.rows(), thresholds())
        self.assertEqual(r["cases"]["S1"][0], "FAIL")
        self.assertIn("PANIC", r["cases"]["S1"][1])
        self.assertEqual(len(r["outages"]), 1)
        self.assertAlmostEqual(r["outages"][0][1], 17, delta=1)   # boot -> CONNECTED

    def test_capture_reopen_reboot_is_expected(self):
        log = Log()
        log.boot(0)
        log.run(0, 1800)
        log.at(1800, f"{soak.MARK} read error: device disconnected; reopening")
        log.boot(1810)                               # reopen resets the board
        log.run(1810, 3600)
        r = soak.analyse(log.rows(), thresholds())
        self.assertEqual(r["cases"]["S1"][0], "PASS")
        # The harness-caused reset is not an outage either.
        self.assertEqual(r["outages"], [])
        self.assertEqual(r["cases"]["S3"][0], "PASS")

    def test_heap_leak_and_failed_alloc_fail_s2(self):
        log = Log()
        log.boot(0)
        log.run(0, 3 * 3600, drift_per_h=-2000)
        self.assertEqual(soak.analyse(log.rows(), thresholds())["cases"]["S2"][0], "FAIL")

        log = Log()
        log.boot(0)
        log.run(0, 3600)
        log.metric(1000, "alloc_failed", size=1696, caps=6144, func="heap_caps_malloc", largest=1280)
        self.assertIn("failed allocation", soak.analyse(log.rows(), thresholds())["cases"]["S2"][1])

    def test_long_outage_fails_s3_and_wifi_recovery_s4(self):
        log = Log()
        log.boot(0)
        log.run(0, 3600)
        log.metric(1000, "wifi_disconnected", reason=201)
        log.metric(1000, "ml_state", state="RECONNECTING")
        log.metric(1290, "wifi_got_ip", rssi=-60)
        log.metric(1300, "ml_state", state="CONNECTED")
        r = soak.analyse(log.rows(), thresholds())
        self.assertEqual(r["cases"]["S3"][0], "FAIL")            # 300 s > 120 s
        self.assertEqual(r["cases"]["S4"][0], "FAIL")            # 290 s > 120 s
        self.assertIn("1 disconnect", r["cases"]["S4"][1])

    def test_timeline_counts_tunnel_packets_per_second(self):
        log = Log()
        log.boot(0)
        log.run(0, 600)                   # filter "accepted" grows by 1 per 10 s sample
        tl = soak.timeline(log.rows())
        full = [row for row in tl if row[0].minute in (2, 3, 4)]
        self.assertTrue(full)
        for _, c0, _, heap, _, acc, alloc in full:
            self.assertAlmostEqual(acc, 0.1, places=2)   # 6 packets / 60 s
            self.assertEqual(alloc, 0)
            self.assertEqual(c0, 2.5)
            self.assertEqual(heap, 60000)

    def test_window_after_boot_is_not_graded_for_availability(self):
        log = Log()
        log.boot(0)
        log.run(0, 1800)
        rows = [row for row in log.rows() if row[0] >= T0 + timedelta(seconds=600)]
        r = soak.analyse(rows, thresholds())
        self.assertEqual(r["cases"]["S3"][0], "WARN")
        self.assertIn("not graded", r["cases"]["S3"][1])
        self.assertIn("unknown (no boot", soak.render(r))

    def test_warnings_grouped_by_message(self):
        log = Log()
        log.boot(0)
        log.run(0, 600)
        for s in (100, 200, 300):
            log.at(s, f"\x1b[0;33mW ({s * 1000}) ml_derp: TLS write timeout after 51 retries\x1b[0m")
        r = soak.analyse(log.rows(), thresholds())
        self.assertEqual(r["logs"][0], ("W ml_derp: TLS write timeout after # retries", 3))


if __name__ == "__main__":
    unittest.main()

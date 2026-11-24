# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Host tests for the MicroLink JSON pruner (tailmote patch):

- test/test_json_prune.c: unit cases, needs only a host C compiler;
- test/fuzz: a short fuzz run (3 s) with the semantic oracles, also needs
  ESP-IDF's cJSON source. Long runs: `tm fuzz --seconds 600`.

Skipped when no host C compiler (gcc / cc / clang; on Windows also MSYS2
MinGW) or, for the fuzz smoke test, no ESP-IDF checkout.
"""
import importlib.util
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("tm", Path(__file__).resolve().parent.parent / "scripts" / "tm.py")
tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tm)

FW = tm.ROOT / tm.FW_PHASE3
ML = FW / "components" / "microlink"


class JsonPruneC(unittest.TestCase):
    def test_host(self):
        cc, path = tm.host_cc()
        if not cc:
            self.skipTest("no host C compiler")
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / ("t.exe" if os.name == "nt" else "t")
            env = dict(os.environ, PATH=path)
            build = subprocess.run(
                [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-O1", f"-I{ML / 'include'}",
                 str(FW / "test" / "test_json_prune.c"), str(ML / "src" / "ml_json_prune.c"), "-o", str(exe)],
                capture_output=True, text=True, env=env)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("PASSED", run.stdout)

    def test_fuzz_smoke(self):
        cc, _ = tm.host_cc()
        if not cc:
            self.skipTest("no host C compiler")
        cjson = Path(tm.project()["IDF_PATH"]) / "components" / "json" / "cJSON" / "cJSON.c"
        if not cjson.is_file():
            self.skipTest("no ESP-IDF cJSON source")
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / ("f.exe" if os.name == "nt" else "f")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                path = tm.build_fuzz_driver(exe)
            corpus = sorted((FW / "test" / "fuzz" / "corpus").glob("*"))
            run = subprocess.run([str(exe), "-t", "3", "-s", "1", *map(str, corpus)],
                                 capture_output=True, text=True, cwd=d, env=dict(os.environ, PATH=path))
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("0 failures", run.stdout)


if __name__ == "__main__":
    unittest.main()

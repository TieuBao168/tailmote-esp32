# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Build and run the C host test of the MicroLink JSON pruner (tailmote patch).

Skipped when no host C compiler (gcc / cc / clang) is on PATH. On Windows the
MSYS2 MinGW gcc at C:\\msys64\\mingw64\\bin is also tried.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FW = Path(__file__).resolve().parent.parent / "firmware" / "phase3-tailscale"
ML = FW / "components" / "microlink"


def find_cc():
    extra = [r"C:\msys64\mingw64\bin", r"C:\msys64\ucrt64\bin"] if os.name == "nt" else []
    path = os.pathsep.join(extra + [os.environ.get("PATH", "")])
    for name in ("gcc", "cc", "clang"):
        exe = shutil.which(name, path=path)
        if exe:
            return exe, path
    return None, path


class JsonPruneC(unittest.TestCase):
    def test_host(self):
        cc, path = find_cc()
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


if __name__ == "__main__":
    unittest.main()

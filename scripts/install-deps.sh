#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Install Ubuntu/Debian packages needed by ESP-IDF and the Linux peer (uses sudo).
# Linux only — called by `make deps` / `scripts/tm.py deps`. Windows needs no system packages.
set -euo pipefail
PKGS=(git wget flex bison gperf python3 python3-venv cmake ninja-build ccache libffi-dev libssl-dev
      dfu-util libusb-1.0-0 wireguard-tools)
command -v apt-get >/dev/null || { echo "error: only apt is supported (Ubuntu/Debian). Other systems: see https://docs.espressif.com/projects/esp-idf/en/v5.3.6/esp32/get-started/linux-macos-setup.html" >&2; exit 1; }
echo "==> sudo apt-get install ${PKGS[*]}"
sudo apt-get update
sudo apt-get install -y "${PKGS[@]}"
if ! id -nG | grep -qw dialout; then
  echo "==> adding $USER to the dialout group (log out and back in for it to take effect)"
  sudo usermod -aG dialout "$USER"
fi

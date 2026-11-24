#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Install Ubuntu/Debian packages needed by ESP-IDF and the Linux peer (uses sudo).
# Linux only — called by `make deps` / `scripts/tm.py deps`. Windows needs no system packages.
set -euo pipefail
PKGS=(git wget flex bison gperf python3 python3-venv cmake ninja-build ccache libffi-dev libssl-dev
      dfu-util libusb-1.0-0 wireguard-tools)
command -v apt-get >/dev/null || { echo "error: chỉ hỗ trợ apt (Ubuntu/Debian). Hệ khác: xem https://docs.espressif.com/projects/esp-idf/en/v5.3.6/esp32/get-started/linux-macos-setup.html" >&2; exit 1; }
echo "==> sudo apt-get install ${PKGS[*]}"
sudo apt-get update
sudo apt-get install -y "${PKGS[@]}"
if ! id -nG | grep -qw dialout; then
  echo "==> thêm $USER vào nhóm dialout (đăng xuất/đăng nhập lại để có hiệu lực)"
  sudo usermod -aG dialout "$USER"
fi

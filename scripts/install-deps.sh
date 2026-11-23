#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Install Ubuntu/Debian packages needed by ESP-IDF and the Linux peer (uses sudo).
. "$(dirname "$0")/common.sh"
PKGS=(git wget flex bison gperf python3 python3-venv cmake ninja-build ccache libffi-dev libssl-dev
      dfu-util libusb-1.0-0 wireguard-tools)
command -v apt-get >/dev/null || die "chỉ hỗ trợ apt (Ubuntu/Debian). Hệ khác: xem https://docs.espressif.com/projects/esp-idf/en/v5.3.6/esp32/get-started/linux-macos-setup.html"
info "sudo apt-get install ${PKGS[*]}"
sudo apt-get update
sudo apt-get install -y "${PKGS[@]}"
if ! id -nG | grep -qw dialout; then
  info "thêm $USER vào nhóm dialout (đăng xuất/đăng nhập lại để có hiệu lực)"
  sudo usermod -aG dialout "$USER"
fi

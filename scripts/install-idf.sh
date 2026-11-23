#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Clone (if needed) and install ESP-IDF + the esp32 toolchain into ~/.espressif.
. "$(dirname "$0")/common.sh"
python3 -c 'import ensurepip, venv' 2>/dev/null || die "thiếu python3-venv — chạy: make deps"
if [[ ! -f "$IDF_PATH/export.sh" ]]; then
  info "clone ESP-IDF $IDF_VERSION → $IDF_PATH (~650 MB)"
  mkdir -p "$(dirname "$IDF_PATH")"
  git clone --depth 1 --branch "$IDF_VERSION" --recurse-submodules --shallow-submodules \
    https://github.com/espressif/esp-idf.git "$IDF_PATH"
else
  ok "đã có ESP-IDF tại $IDF_PATH"
fi
info "install.sh $IDF_TARGET (toolchain ~1.5 GB vào ~/.espressif)"
"$IDF_PATH/install.sh" "$IDF_TARGET"
ok "xong. Kiểm tra: make doctor"

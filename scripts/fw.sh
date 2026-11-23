#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Firmware actions: build | size | flash | monitor | flash-monitor | menuconfig | clean | fullclean
. "$(dirname "$0")/common.sh"
action="${1:-build}"

# sdkconfig.defaults/.secrets only apply when sdkconfig does not exist.
# Regenerate sdkconfig whenever either input changed since the last build.
refresh_sdkconfig() {
  local stamp="$FW_DIR/build/.sdkconfig-inputs.sha" sum
  sum="$(cat "$FW_DIR/sdkconfig.defaults" "$FW_DIR/sdkconfig.secrets" 2>/dev/null | sha256sum | cut -d' ' -f1)"
  if [[ -f "$FW_DIR/sdkconfig" && ( ! -f "$stamp" || "$(cat "$stamp")" != "$sum" ) ]]; then
    info "sdkconfig.defaults/secrets đã đổi → tạo lại sdkconfig (thay đổi menuconfig cũ sẽ mất)"
    rm -f "$FW_DIR/sdkconfig"
  fi
  mkdir -p "$FW_DIR/build"
  echo "$sum" > "$stamp"
}

port_args() {
  [[ -f "$SECRETS_FILE" ]] && load_secrets
  echo "-p ${SERIAL_PORT:-/dev/ttyUSB0} -b ${FLASH_BAUD:-460800}"
}

idf_env
cd "$FW_DIR"
case "$action" in
  build)
    [[ -f sdkconfig.secrets ]] || info "CẢNH BÁO: chưa có sdkconfig.secrets (make config) — firmware sẽ không kết nối Wi-Fi/WireGuard"
    refresh_sdkconfig
    [[ -f sdkconfig ]] || idf.py set-target "$IDF_TARGET"
    idf.py build
    ;;
  size)
    mkdir -p "$RESULTS_DIR"
    idf.py size | tee "$RESULTS_DIR/size.txt"
    idf.py size-components > "$RESULTS_DIR/size-components.txt"
    ok "lưu: results/size.txt, results/size-components.txt"
    ;;
  flash)         idf.py $(port_args) flash ;;
  monitor)       idf.py $(port_args) monitor ;;
  flash-monitor) idf.py $(port_args) flash monitor ;;
  menuconfig)    idf.py menuconfig ;;
  clean)         idf.py clean ;;
  fullclean)     idf.py fullclean; rm -f sdkconfig sdkconfig.old ;;
  *) die "không rõ action: $action" ;;
esac

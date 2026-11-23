#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Check everything needed to build / flash / run. Changes nothing.
. "$(dirname "$0")/common.sh"
missing=0
need() { if "$@" >/dev/null 2>&1; then return 0; else missing=$((missing + 1)); return 1; fi; }

info "Hệ thống"
need command -v git && ok "git" || bad "git"
need command -v wget && ok "wget" || bad "wget"
need command -v python3 && ok "python3 $(python3 -c 'import sys;print(sys.version.split()[0])')" || bad "python3"
need python3 -c 'import ensurepip, venv' && ok "python3-venv" || bad "python3-venv"
for p in flex bison gperf libffi-dev libssl-dev libusb-1.0-0 dfu-util ccache; do
  need dpkg -s "$p" && ok "$p" || bad "$p"
done
free_gb=$(df -Pk "$HOME" | awk 'NR==2{print int($4/1048576)}')
(( free_gb >= 4 )) && ok "dung lượng trống ${free_gb} GB" || { bad "dung lượng trống ${free_gb} GB (< 4 GB)"; missing=$((missing + 1)); }

info "ESP-IDF $IDF_VERSION ($IDF_PATH)"
if [[ -f "$IDF_PATH/export.sh" ]]; then
  ok "source"
  if ls "$HOME"/.espressif/tools/xtensa-esp-elf/*/xtensa-esp-elf/bin/xtensa-esp32-elf-gcc >/dev/null 2>&1; then
    ok "toolchain xtensa-esp32"
  else
    bad "toolchain chưa cài (make idf)"; missing=$((missing + 1))
  fi
  ls "$HOME"/.espressif/python_env/idf5.3_py*_env >/dev/null 2>&1 && ok "python env" || { bad "python env chưa tạo (make idf)"; missing=$((missing + 1)); }
else
  bad "chưa có (make idf)"; missing=$((missing + 1))
fi

info "Cấu hình"
[[ -f "$SECRETS_FILE" ]] && ok "form $SECRETS_FILE" || bad "form chưa có (make secrets)"
[[ -f "$FW_DIR/sdkconfig.secrets" ]] && ok "sdkconfig.secrets" || bad "sdkconfig.secrets chưa sinh (make config) — build vẫn được nhưng firmware không kết nối"

info "Chạy (không cần để build)"
command -v wg >/dev/null && ok "wireguard-tools" || bad "wireguard-tools"
id -nG | grep -qw dialout && ok "user thuộc nhóm dialout" || bad "user chưa thuộc nhóm dialout: sudo usermod -aG dialout $USER"
port="/dev/ttyUSB0"; [[ -f "$SECRETS_FILE" ]] && { load_secrets; port="$SERIAL_PORT"; }
[[ -e "$port" ]] && ok "serial $port" || bad "không thấy $port (chưa cắm board?)"

echo
if (( missing == 0 )); then info "đủ điều kiện build"; else info "thiếu $missing mục để build — xem ✘ ở trên (make deps, make idf)"; exit 1; fi

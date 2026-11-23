# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Shared helpers for scripts/*.sh (sourced, not executed).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../config/project.env
. "$ROOT/config/project.env"
SECRETS_DIR="$ROOT/secrets"
SECRETS_FILE="$SECRETS_DIR/secrets.env"
SECRETS_FORM="$ROOT/config/secrets.env.example"
FW_DIR="$ROOT/$FW_PROJECT"
RESULTS_DIR="$ROOT/results"

ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✘\033[0m %s\n' "$*"; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

load_secrets() {
  [[ -f "$SECRETS_FILE" ]] || die "chưa có $SECRETS_FILE — chạy: make secrets"
  set -a
  # shellcheck disable=SC1090
  . "$SECRETS_FILE"
  set +a
  WG_PORT="${WG_PORT:-51820}"
  LINUX_TUNNEL_IP="${LINUX_TUNNEL_IP:-10.66.0.1}"
  ESP_TUNNEL_IP="${ESP_TUNNEL_IP:-10.66.0.2}"
  TUNNEL_NETMASK="${TUNNEL_NETMASK:-255.255.255.0}"
  TUNNEL_PREFIX="${TUNNEL_PREFIX:-24}"
  WG_KEEPALIVE="${WG_KEEPALIVE:-25}"
  WIFI_POWER_SAVE="${WIFI_POWER_SAVE:-none}"
  SERIAL_PORT="${SERIAL_PORT:-/dev/ttyUSB0}"
  FLASH_BAUD="${FLASH_BAUD:-460800}"
}

# Make idf.py available in this shell.
idf_env() {
  if command -v idf.py >/dev/null 2>&1; then return; fi
  [[ -f "$IDF_PATH/export.sh" ]] || die "không thấy ESP-IDF tại $IDF_PATH — chạy: make idf"
  # shellcheck disable=SC1091
  . "$IDF_PATH/export.sh" >/dev/null 2>&1 || die "export.sh lỗi — chạy lại: make idf"
}

#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Create secrets/secrets.env from the form (never overwrites an existing one).
. "$(dirname "$0")/common.sh"

umask 077
mkdir -p "$SECRETS_DIR"
if [[ -f "$SECRETS_FILE" ]]; then
  info "đã có $SECRETS_FILE (giữ nguyên). Sửa file đó rồi chạy: make config"
  exit 0
fi
cp "$SECRETS_FORM" "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
info "đã tạo form: $SECRETS_FILE"
echo "    Điền ít nhất WIFI_SSID / WIFI_PASSWORD, sau đó chạy: make config"

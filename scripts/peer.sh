#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Linux WireGuard peer: up | down | status
. "$(dirname "$0")/common.sh"
conf="$SECRETS_DIR/wg-esp.conf"
[[ -f "$conf" ]] || die "chưa có $conf — chạy: make config"
case "${1:-status}" in
  up)     sudo wg-quick up "$conf" ;;
  down)   sudo wg-quick down "$conf" ;;
  status) sudo wg show wg-esp ;;
  *) die "up | down | status" ;;
esac

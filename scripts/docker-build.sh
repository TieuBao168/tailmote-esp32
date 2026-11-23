#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# Build the firmware inside Espressif's official image (only Docker needed).
. "$(dirname "$0")/common.sh"
command -v docker >/dev/null || die "chưa cài docker"
[[ -f "$FW_DIR/sdkconfig.secrets" ]] || info "CẢNH BÁO: chưa có sdkconfig.secrets (make config)"
info "espressif/idf:$IDF_VERSION  →  $FW_PROJECT/build"
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$FW_DIR:/project" -w /project "espressif/idf:$IDF_VERSION" \
  bash -c "[ -f sdkconfig ] || idf.py set-target $IDF_TARGET; idf.py build"

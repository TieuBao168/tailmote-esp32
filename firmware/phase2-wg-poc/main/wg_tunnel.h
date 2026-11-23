// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
#pragma once

#include <stdbool.h>

/* Start the tunnel supervisor: SNTP -> WireGuard connect -> monitor/reconnect. */
void wg_tunnel_start(void);

bool wg_tunnel_is_up(void);

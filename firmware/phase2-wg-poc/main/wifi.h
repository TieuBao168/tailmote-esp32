// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
#pragma once

#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#define WIFI_GOT_IP_BIT BIT0
#define WIFI_LOST_IP_BIT BIT1

/* Start Wi-Fi STA; reconnects forever with exponential backoff. */
void wifi_start(void);

EventGroupHandle_t wifi_events(void);
bool wifi_has_ip(void);

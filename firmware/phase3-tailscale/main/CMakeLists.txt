# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
idf_component_register(
    SRCS "main.c" "metrics.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES microlink nvs_flash esp_wifi esp_netif esp_event esp_timer spi_flash esp_app_format)

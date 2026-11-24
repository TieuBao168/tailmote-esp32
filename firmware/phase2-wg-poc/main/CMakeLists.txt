# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
idf_component_register(
    SRCS "main.c" "wifi.c" "wg_tunnel.c" "metrics.c" "bench.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_wireguard esp_wifi esp_netif esp_event esp_timer nvs_flash lwip spi_flash esp_app_format)

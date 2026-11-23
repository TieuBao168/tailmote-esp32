// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
/*
 * Phase 2 - WireGuard PoC on ESP32 DOIT DevKit V1.
 *
 * Boot -> Wi-Fi -> SNTP -> WireGuard tunnel to a Linux peer, plus benchmark
 * services and METRIC lines for RAM / CPU / timing / throughput.
 */
#include <stdio.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "nvs_flash.h"
#include "metrics.h"
#include "wifi.h"
#include "wg_tunnel.h"
#include "bench.h"

static void report_chip(void)
{
    esp_chip_info_t ci;
    esp_chip_info(&ci);
    uint32_t flash = 0;
    esp_flash_get_size(NULL, &flash);
    char extra[200];
    snprintf(extra, sizeof(extra),
             "\"model\":%d,\"rev\":%d,\"cores\":%d,\"flash\":%" PRIu32 ",\"idf\":\"%s\",\"reset\":%d,\"app\":\"%s\"",
             ci.model, ci.revision, ci.cores, flash, esp_get_idf_version(), esp_reset_reason(),
             esp_app_get_description()->version);
    metrics_event("boot", extra);
}

void app_main(void)
{
    report_chip();

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    metrics_event("base_init", NULL);

    metrics_heap_t before = metrics_heap_now();
    wifi_start();
    metrics_heap_delta("wifi_start", before);

    metrics_start();

    before = metrics_heap_now();
    bench_start();
    metrics_heap_delta("bench_tasks", before);

    wg_tunnel_start();
}

// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
#include "wg_tunnel.h"

#include <stdio.h>
#include <string.h>
#include <time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "esp_wireguard.h"
#include "sdkconfig.h"
#include "metrics.h"
#include "wifi.h"

static const char *TAG = "wg";

static wireguard_config_t s_cfg = ESP_WIREGUARD_CONFIG_DEFAULT();
static wireguard_ctx_t s_ctx;
static volatile bool s_up;
static uint32_t s_sessions;

bool wg_tunnel_is_up(void)
{
    return s_up;
}

/* WireGuard handshakes carry a TAI64N timestamp; the Linux peer rejects an
 * initiation older than the last one it accepted, so time must be correct
 * before every connect (especially after a reboot). */
static bool sync_time(void)
{
    static bool sntp_started;
    uint32_t t0 = metrics_ms();
    if (!sntp_started) {
        esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
        if (esp_netif_sntp_init(&cfg) != ESP_OK) {
            return false;
        }
        sntp_started = true;
    } else if (time(NULL) > 1700000000) {
        return true; /* already synced, SNTP keeps it updated */
    }
    if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(20000)) != ESP_OK) {
        metrics_event("sntp_timeout", NULL);
        return false;
    }
    char extra[48];
    snprintf(extra, sizeof(extra), "\"took_ms\":%lu", (unsigned long)(metrics_ms() - t0));
    metrics_event("sntp_synced", extra);
    return true;
}

static void config_init(void)
{
    s_cfg.private_key = CONFIG_POC_WG_PRIVATE_KEY;
    s_cfg.listen_port = CONFIG_POC_WG_LISTEN_PORT;
    s_cfg.public_key = CONFIG_POC_WG_PEER_PUBLIC_KEY;
    s_cfg.preshared_key = strlen(CONFIG_POC_WG_PRESHARED_KEY) ? CONFIG_POC_WG_PRESHARED_KEY : NULL;
    s_cfg.allowed_ip = CONFIG_POC_WG_LOCAL_IP;
    s_cfg.allowed_ip_mask = CONFIG_POC_WG_LOCAL_NETMASK;
    s_cfg.endpoint = CONFIG_POC_WG_PEER_ENDPOINT;
    s_cfg.port = CONFIG_POC_WG_PEER_PORT;
    s_cfg.persistent_keepalive = CONFIG_POC_WG_KEEPALIVE;
}

/* Connect and wait for the first completed handshake. */
static bool connect_and_wait(void)
{
    metrics_heap_t before = metrics_heap_now();
    uint32_t t0 = metrics_ms();
    esp_err_t err = esp_wireguard_connect(&s_ctx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wireguard_connect: %s", esp_err_to_name(err));
        return false;
    }
    metrics_heap_delta("wg_connect", before);

    for (int i = 0; i < 200; i++) { /* up to 20 s */
        if (esp_wireguardif_peer_is_up(&s_ctx) == ESP_OK) {
            s_sessions++;
            char extra[64];
            snprintf(extra, sizeof(extra), "\"handshake_ms\":%lu,\"session\":%lu",
                     (unsigned long)(metrics_ms() - t0), (unsigned long)s_sessions);
            metrics_event("wg_up", extra);
            metrics_heap_delta("wg_session", before);
            s_up = true;
            return true;
        }
        if (!wifi_has_ip()) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    metrics_event("wg_handshake_timeout", NULL);
    return false;
}

static void disconnect(const char *why)
{
    s_up = false;
    char extra[48];
    snprintf(extra, sizeof(extra), "\"why\":\"%s\"", why);
    metrics_event("wg_down", extra);
    if (s_ctx.netif) {
        esp_wireguard_disconnect(&s_ctx);
    }
}

static void supervisor_task(void *arg)
{
    config_init();
    ESP_ERROR_CHECK(esp_wireguard_init(&s_cfg, &s_ctx));

    uint32_t peer_down_since = 0;
    for (;;) {
        /* 1. Underlay */
        xEventGroupWaitBits(wifi_events(), WIFI_GOT_IP_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
        if (!sync_time()) {
            vTaskDelay(pdMS_TO_TICKS(2000));
            continue;
        }

        /* 2. Tunnel */
        if (!connect_and_wait()) {
            disconnect("handshake_failed");
            vTaskDelay(pdMS_TO_TICKS(3000));
            continue;
        }

        /* 3. Monitor */
        peer_down_since = 0;
        for (;;) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            if (!wifi_has_ip()) {
                disconnect("wifi_lost");
                break;
            }
            bool up = esp_wireguardif_peer_is_up(&s_ctx) == ESP_OK;
            if (up != s_up) {
                metrics_event(up ? "wg_peer_up" : "wg_peer_stale", NULL);
                s_up = up;
            }
            if (!up) {
                if (!peer_down_since) {
                    peer_down_since = metrics_ms();
                } else if (metrics_ms() - peer_down_since > CONFIG_POC_WG_PEER_DOWN_RESTART_S * 1000U) {
                    disconnect("peer_timeout");
                    break;
                }
            } else {
                peer_down_since = 0;
            }
        }
    }
}

void wg_tunnel_start(void)
{
    /* esp_wireguard_connect() runs the NaCl reference X25519 (public key from the
     * private key) on this task's stack: 4096 B overflowed on the first connect. */
    xTaskCreatePinnedToCore(supervisor_task, "wg_sup", 8192, NULL, 5, NULL, 1);
}

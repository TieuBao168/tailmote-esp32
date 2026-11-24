// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
/*
 * Phase 3 - ESP32 DOIT DevKit V1 as a Tailscale node (MicroLink).
 *
 * Boot -> Wi-Fi -> MicroLink (ts2021 register, MapResponse, DERP, DISCO).
 * The first job of this firmware is measurement: every stage prints a
 * METRIC line with free / largest internal heap (docs/03 section 3, risk 1:
 * the MapResponse cJSON tree on a board without PSRAM).
 *
 * Test service: UDP echo on CONFIG_TM_ECHO_PORT (default 7) of the tailnet IP:
 *   python tools/bench/wg_bench.py rtt --host <100.x.y.z>
 */
#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_wifi.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "microlink.h"
#include "metrics.h"

static const char *TAG = "tm";

static EventGroupHandle_t s_wifi_ev;
#define WIFI_GOT_IP_BIT BIT0

static microlink_t *s_ml;
static microlink_udp_socket_t *s_echo;
static volatile uint32_t s_echo_rx;

/* ------------------------------------------------------------- chip info -- */
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

/* ------------------------------------------------------------------ Wi-Fi -- */
static void wifi_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d = data;
        char extra[32];
        snprintf(extra, sizeof(extra), "\"reason\":%d", d->reason);
        metrics_event("wifi_disconnected", extra);
        xEventGroupClearBits(s_wifi_ev, WIFI_GOT_IP_BIT);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *e = data;
        wifi_ap_record_t ap = {0};
        esp_wifi_sta_get_ap_info(&ap);
        char extra[112];
        snprintf(extra, sizeof(extra),
                 "\"ip\":\"" IPSTR "\",\"rssi\":%d,\"channel\":%d,\"bssid\":\"" MACSTR "\"",
                 IP2STR(&e->ip_info.ip), ap.rssi, ap.primary, MAC2STR(ap.bssid));
        metrics_event("wifi_got_ip", extra);
        xEventGroupSetBits(s_wifi_ev, WIFI_GOT_IP_BIT);
    }
}

static void wifi_start(void)
{
    s_wifi_ev = xEventGroupCreate();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_handler, NULL));
    /* Mesh networks expose several APs with the same SSID. The default
     * fast scan joins the first one found, which here was often a far node
     * at -77..-82 dBm (TCP stalls); a full scan sorted by signal picks the
     * nearest one (seen at -53 dBm in Phase 2). */
    wifi_config_t wc = { .sta = {
        .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        .scan_method = WIFI_ALL_CHANNEL_SCAN,
        .sort_method = WIFI_CONNECT_AP_BY_SIGNAL,
    } };
    strlcpy((char *)wc.sta.ssid, CONFIG_ML_WIFI_SSID, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, CONFIG_ML_WIFI_PASSWORD, sizeof(wc.sta.password));
    if (!strlen(CONFIG_ML_WIFI_PASSWORD)) {
        wc.sta.threshold.authmode = WIFI_AUTH_OPEN;
    }
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_LOGI(TAG, "connecting to \"%s\"", CONFIG_ML_WIFI_SSID);
}

/* -------------------------------------------------------------- MicroLink -- */
static const char *state_name(microlink_state_t s)
{
    static const char *names[] = {
        "IDLE", "WIFI_WAIT", "CONNECTING", "REGISTERING", "CONNECTED",
        "RECONNECTING", "ERROR", "AUTH_FAILED", "NEEDS_LOGIN",
    };
    return (unsigned)s < sizeof(names) / sizeof(names[0]) ? names[s] : "UNKNOWN";
}

static void on_state(microlink_t *ml, microlink_state_t state, void *arg)
{
    char extra[96];
    if (state == ML_STATE_CONNECTED) {
        char ip[16];
        microlink_ip_to_str(microlink_get_vpn_ip(ml), ip);
        snprintf(extra, sizeof(extra), "\"state\":\"%s\",\"vpn_ip\":\"%s\",\"peers\":%d",
                 state_name(state), ip, microlink_get_peer_count(ml));
    } else {
        snprintf(extra, sizeof(extra), "\"state\":\"%s\"", state_name(state));
    }
    metrics_event("ml_state", extra);

    if (state == ML_STATE_NEEDS_LOGIN) {
        /* No auth key: printed only on the serial console, for whoever holds
         * the board. Opening the URL adds the device to that tailnet. */
        char url[MICROLINK_AUTH_URL_MAX];
        if (microlink_get_auth_url(ml, url, sizeof(url)) == ESP_OK) {
            printf("\nTo add this device to your tailnet, open:\n    %s\n\n", url);
        }
    }
}

static void on_peer(microlink_t *ml, const microlink_peer_info_t *p, void *arg)
{
    char ip[16];
    microlink_ip_to_str(p->vpn_ip, ip);
    char extra[96];
    snprintf(extra, sizeof(extra), "\"ip\":\"%s\",\"online\":%d,\"direct\":%d",
             ip, p->online, p->direct_path);
    metrics_event("ml_peer", extra);
}

static void on_echo_rx(microlink_udp_socket_t *sock, uint32_t src_ip, uint16_t src_port,
                       const uint8_t *data, size_t len, void *arg)
{
    s_echo_rx++;
    microlink_udp_send(sock, src_ip, src_port, data, len);
}

/* Once connected: open the echo socket and report the heap after the first
 * netmap, which is the number docs/03 section 3 asks for. */
static void supervisor_task(void *arg)
{
    bool reported = false;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        bool up = microlink_is_connected(s_ml);
        if (up && !s_echo) {
            metrics_heap_t before = metrics_heap_now();
            s_echo = microlink_udp_create(s_ml, CONFIG_TM_ECHO_PORT);
            if (s_echo) {
                microlink_udp_set_rx_callback(s_echo, on_echo_rx, NULL);
                metrics_heap_delta("echo_socket", before);
            }
        }
        if (up && !reported) {
            char extra[48];
            snprintf(extra, sizeof(extra), "\"peers\":%d", microlink_get_peer_count(s_ml));
            metrics_event("ml_first_netmap", extra);
            reported = true;
        }
    }
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

    xEventGroupWaitBits(s_wifi_ev, WIFI_GOT_IP_BIT, pdFALSE, pdTRUE, portMAX_DELAY);

    microlink_config_t cfg = {
        .auth_key = CONFIG_ML_TAILSCALE_AUTH_KEY,   /* "" = interactive login URL */
        .device_name = CONFIG_ML_DEVICE_NAME,
        .enable_derp = true,
        .enable_stun = true,
        .enable_disco = true,
        .max_peers = CONFIG_ML_MAX_PEERS,
    };
    before = metrics_heap_now();
    s_ml = microlink_init(&cfg);
    if (!s_ml) {
        metrics_event("ml_init_failed", NULL);
        return;
    }
    metrics_heap_delta("ml_init", before);
    microlink_set_state_callback(s_ml, on_state, NULL);
    microlink_set_peer_callback(s_ml, on_peer, NULL);

    before = metrics_heap_now();
    err = microlink_start(s_ml);
    if (err != ESP_OK) {
        char extra[48];
        snprintf(extra, sizeof(extra), "\"err\":\"%s\"", esp_err_to_name(err));
        metrics_event("ml_start_failed", extra);
        return;
    }
    metrics_heap_delta("ml_start", before);

    xTaskCreatePinnedToCore(supervisor_task, "tm_sup", 3072, NULL, 3, NULL, 0);
}

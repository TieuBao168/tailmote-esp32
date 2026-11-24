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
#include <stdlib.h>
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
#include "esp_heap_caps.h"
#include "esp_rom_sys.h"
#include "nvs_flash.h"
#include "microlink.h"
#include "metrics.h"

static const char *TAG = "tm";

static EventGroupHandle_t s_wifi_ev;
#define WIFI_GOT_IP_BIT BIT0

static microlink_t *s_ml;
static microlink_udp_socket_t *s_echo;
static volatile uint32_t s_echo_rx;
static volatile bool s_wifi_hold;   /* test hook: Wi-Fi kept down */

/* UDP sink counters (same STAT JSON as Phase 2 bench.c, for wg_bench.py udp-rx) */
static struct {
    uint32_t pkts, max_seq, reorder, last_seq, t_first, t_last;
    uint64_t bytes;
} s_sink;

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
/* After 3 "no AP found" in a row, scan once and log what the board can see,
 * so a wrong SSID, a 5 GHz-only network or an unsupported security mode is
 * obvious from the serial log. Runs in its own task: a scan cannot run while
 * the event handler keeps calling esp_wifi_connect(). */
static volatile bool s_scanning;

static const char *auth_name(wifi_auth_mode_t m)
{
    switch (m) {
    case WIFI_AUTH_OPEN: return "open";
    case WIFI_AUTH_WEP: return "WEP";
    case WIFI_AUTH_WPA_PSK: return "WPA";
    case WIFI_AUTH_WPA2_PSK: return "WPA2";
    case WIFI_AUTH_WPA_WPA2_PSK: return "WPA/WPA2";
    case WIFI_AUTH_WPA3_PSK: return "WPA3";
    case WIFI_AUTH_WPA2_WPA3_PSK: return "WPA2/WPA3";
    default: return "other";
    }
}

static void wifi_scan_task(void *arg)
{
    wifi_scan_config_t sc = { .show_hidden = true };
    esp_wifi_disconnect();
    if (esp_wifi_scan_start(&sc, true) == ESP_OK) {
        uint16_t total = 0, n = 40;
        static wifi_ap_record_t recs[40];
        esp_wifi_scan_get_ap_num(&total);
        esp_wifi_scan_get_ap_records(&n, recs);
        printf("Wi-Fi scan: %u networks, showing %u (looking for \"%s\")\n", total, n, CONFIG_ML_WIFI_SSID);
        for (uint16_t i = 0; i < n; i++) {
            printf("METRIC {\"ev\":\"wifi_scan\",\"ssid\":\"%.32s\",\"rssi\":%d,\"channel\":%d,\"auth\":\"%s\"}\n",
                   (const char *)recs[i].ssid, recs[i].rssi, recs[i].primary, auth_name(recs[i].authmode));
        }
    }
    s_scanning = false;
    esp_wifi_connect();
    vTaskDelete(NULL);
}

static void wifi_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    static int no_ap_count;
    static bool scanned;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d = data;
        char extra[32];
        snprintf(extra, sizeof(extra), "\"reason\":%d", d->reason);
        metrics_event("wifi_disconnected", extra);
        xEventGroupClearBits(s_wifi_ev, WIFI_GOT_IP_BIT);
        no_ap_count = (d->reason == WIFI_REASON_NO_AP_FOUND) ? no_ap_count + 1 : 0;
        if (no_ap_count >= 3 && !scanned &&
            xTaskCreate(wifi_scan_task, "tm_scan", 3072, NULL, 3, NULL) == pdPASS) {
            scanned = true;
            s_scanning = true;
            return;
        }
        if (!s_wifi_hold && !s_scanning) {   /* test hook WIFI_DOWN keeps it down */
            esp_wifi_connect();
        }
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

#if CONFIG_TM_TEST_HOOKS
/* Drop Wi-Fi for `secs` from the device side, then reconnect: recovery test
 * for Wi-Fi loss without touching the access point. */
static void wifi_down_task(void *arg)
{
    uint32_t secs = (uint32_t)(uintptr_t)arg;
    vTaskDelay(pdMS_TO_TICKS(1000));   /* let the "OK" reply leave first */
    char extra[32];
    snprintf(extra, sizeof(extra), "\"secs\":%u", (unsigned)secs);
    metrics_event("test_wifi_down", extra);
    s_wifi_hold = true;
    esp_wifi_disconnect();
    vTaskDelay(pdMS_TO_TICKS(secs * 1000));
    s_wifi_hold = false;
    metrics_event("test_wifi_up", NULL);
    esp_wifi_connect();
    vTaskDelete(NULL);
}
#endif

/* One socket (each MicroLink UDP socket costs ~11 KB of RAM) multiplexes:
 *   "RST" / "STAT"      UDP sink control (reply "OK" / JSON), as Phase 2;
 *   "SINK"+u32 seq+...  sink data, counted, no reply (wg_bench.py --sink-prefix SINK);
 *   "WIFI_DOWN <s>"     test hook (CONFIG_TM_TEST_HOOKS), reply "OK";
 *   anything else       echoed back (wg_bench.py rtt). */
static void on_echo_rx(microlink_udp_socket_t *sock, uint32_t src_ip, uint16_t src_port,
                       const uint8_t *data, size_t len, void *arg)
{
    if (len == 3 && memcmp(data, "RST", 3) == 0) {
        memset(&s_sink, 0, sizeof(s_sink));
        microlink_udp_send(sock, src_ip, src_port, "OK", 2);
        return;
    }
    if (len == 4 && memcmp(data, "STAT", 4) == 0) {
        char out[160];
        int n = snprintf(out, sizeof(out),
                         "{\"pkts\":%" PRIu32 ",\"bytes\":%" PRIu64 ",\"max_seq\":%" PRIu32
                         ",\"reorder\":%" PRIu32 ",\"dur_ms\":%" PRIu32 "}",
                         s_sink.pkts, s_sink.bytes, s_sink.max_seq, s_sink.reorder,
                         s_sink.t_last - s_sink.t_first);
        microlink_udp_send(sock, src_ip, src_port, out, n);
        return;
    }
    if (len >= 8 && memcmp(data, "SINK", 4) == 0) {
        uint32_t seq = ((uint32_t)data[4] << 24) | ((uint32_t)data[5] << 16) |
                       ((uint32_t)data[6] << 8) | data[7];
        uint32_t now = metrics_ms();
        if (s_sink.pkts && seq < s_sink.last_seq) {
            s_sink.reorder++;
        }
        if (!s_sink.pkts) {
            s_sink.t_first = now;
        }
        s_sink.last_seq = seq;
        s_sink.max_seq = seq > s_sink.max_seq ? seq : s_sink.max_seq;
        s_sink.t_last = now;
        s_sink.pkts++;
        s_sink.bytes += len;
        return;
    }
#if CONFIG_TM_TEST_HOOKS
    if (len > 10 && len < 16 && memcmp(data, "WIFI_DOWN ", 10) == 0) {
        char num[8] = {0};
        memcpy(num, data + 10, len - 10);
        uint32_t secs = (uint32_t)atoi(num);
        if (secs >= 1 && secs <= 600 &&
            xTaskCreate(wifi_down_task, "tm_wifidn", 2560, (void *)(uintptr_t)secs, 3, NULL) == pdPASS) {
            microlink_udp_send(sock, src_ip, src_port, "OK", 2);
        }
        return;
    }
#endif
    s_echo_rx++;
    microlink_udp_send(sock, src_ip, src_port, data, len);
}

/* Any malloc that fails is a RAM-budget bug on this board: report it. Runs in
 * the failing caller's context, so only ROM printf (no heap). */
static void on_alloc_failed(size_t size, uint32_t caps, const char *func)
{
    /* MicroLink's ml_psram_malloc() asks for SPIRAM first and falls back to
     * internal RAM; on this board (no PSRAM) every such probe "fails". */
    if (caps & MALLOC_CAP_SPIRAM) {
        return;
    }
    esp_rom_printf("METRIC {\"ev\":\"alloc_failed\",\"t_ms\":%u,\"size\":%u,\"caps\":%u,\"func\":\"%s\",\"largest\":%u}\n",
                   (unsigned)metrics_ms(), (unsigned)size, (unsigned)caps, func ? func : "?",
                   (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
}

/* Report each new heap low-water mark (>= 2 KB lower than the last report)
 * with the MicroLink state, to find what drives the startup minimum. */
static void report_heap_low(void)
{
    static size_t last = SIZE_MAX;
    size_t min = heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT);
    if (last == SIZE_MAX || min + 2048 <= last) {
        char extra[64];
        snprintf(extra, sizeof(extra), "\"heap_min\":%u,\"state\":\"%s\"",
                 (unsigned)min, state_name(microlink_get_state(s_ml)));
        metrics_event("heap_low", extra);
        last = min;
    }
}

/* Once connected: open the echo socket and report the heap after the first
 * netmap, which is the number docs/03 section 3 asks for. */
static void supervisor_task(void *arg)
{
    bool reported = false;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(250));
        report_heap_low();
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
    heap_caps_register_failed_alloc_callback(on_alloc_failed);
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

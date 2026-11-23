// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
#include "wifi.h"

#include <string.h>
#include <stdio.h>
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "sdkconfig.h"
#include "metrics.h"

static const char *TAG = "wifi";

static EventGroupHandle_t s_events;
static volatile bool s_has_ip;
static esp_timer_handle_t s_retry_timer;
static uint32_t s_backoff_ms = 500;
static uint32_t s_disconnects;

#define BACKOFF_MAX_MS 30000

static void retry_cb(void *arg)
{
    esp_wifi_connect();
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = data;
        s_disconnects++;
        if (s_has_ip) {
            s_has_ip = false;
            xEventGroupClearBits(s_events, WIFI_GOT_IP_BIT);
            xEventGroupSetBits(s_events, WIFI_LOST_IP_BIT);
        }
        char extra[64];
        snprintf(extra, sizeof(extra), "\"reason\":%d,\"retry_ms\":%lu,\"count\":%lu",
                 d->reason, (unsigned long)s_backoff_ms, (unsigned long)s_disconnects);
        metrics_event("wifi_disconnected", extra);
        esp_timer_start_once(s_retry_timer, (uint64_t)s_backoff_ms * 1000);
        s_backoff_ms = s_backoff_ms * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : s_backoff_ms * 2;
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        wifi_ap_record_t ap;
        int rssi = (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) ? ap.rssi : 0;
        char extra[96];
        snprintf(extra, sizeof(extra), "\"ip\":\"" IPSTR "\",\"rssi\":%d,\"channel\":%d",
                 IP2STR(&e->ip_info.ip), rssi, rssi ? ap.primary : 0);
        metrics_event("wifi_got_ip", extra);
        s_backoff_ms = 500;
        s_has_ip = true;
        xEventGroupClearBits(s_events, WIFI_LOST_IP_BIT);
        xEventGroupSetBits(s_events, WIFI_GOT_IP_BIT);
    }
}

void wifi_start(void)
{
    s_events = xEventGroupCreate();

    const esp_timer_create_args_t targs = { .callback = retry_cb, .name = "wifi_retry" };
    ESP_ERROR_CHECK(esp_timer_create(&targs, &s_retry_timer));

    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));

    wifi_config_t wc = { 0 };
    strlcpy((char *)wc.sta.ssid, CONFIG_POC_WIFI_SSID, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, CONFIG_POC_WIFI_PASSWORD, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = strlen(CONFIG_POC_WIFI_PASSWORD) ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    wc.sta.pmf_cfg.capable = true;

    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
#if CONFIG_POC_WIFI_PS_MIN_MODEM
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_MIN_MODEM));
#else
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
#endif
    ESP_LOGI(TAG, "connecting to \"%s\"", CONFIG_POC_WIFI_SSID);
    ESP_ERROR_CHECK(esp_wifi_start());
}

EventGroupHandle_t wifi_events(void)
{
    return s_events;
}

bool wifi_has_ip(void)
{
    return s_has_ip;
}

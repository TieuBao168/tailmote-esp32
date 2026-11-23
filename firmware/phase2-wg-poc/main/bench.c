// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
#include "bench.h"

#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "esp_log.h"
#include "sdkconfig.h"
#include "metrics.h"

static const char *TAG = "bench";

#define UDP_ECHO_PORT 7
#define UDP_SINK_PORT 5001
#define TCP_SINK_PORT 5002
#define TCP_SRC_PORT 5003

static int udp_bind(uint16_t port)
{
    int s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s < 0) {
        return -1;
    }
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(port), .sin_addr.s_addr = htonl(INADDR_ANY) };
    if (bind(s, (struct sockaddr *)&a, sizeof(a)) < 0) {
        close(s);
        return -1;
    }
    return s;
}

static int tcp_listen(uint16_t port)
{
    int s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s < 0) {
        return -1;
    }
    int one = 1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(port), .sin_addr.s_addr = htonl(INADDR_ANY) };
    if (bind(s, (struct sockaddr *)&a, sizeof(a)) < 0 || listen(s, 1) < 0) {
        close(s);
        return -1;
    }
    return s;
}

/* ---- UDP echo ---- */
static void udp_echo_task(void *arg)
{
    static uint8_t buf[1500];
    int s = udp_bind(UDP_ECHO_PORT);
    if (s < 0) {
        ESP_LOGE(TAG, "udp echo bind failed");
        vTaskDelete(NULL);
    }
    for (;;) {
        struct sockaddr_in from;
        socklen_t fl = sizeof(from);
        int n = recvfrom(s, buf, sizeof(buf), 0, (struct sockaddr *)&from, &fl);
        if (n > 0) {
            sendto(s, buf, n, 0, (struct sockaddr *)&from, fl);
        }
    }
}

/* ---- UDP sink: data packets start with a big-endian u32 sequence number.
 *      Control packets: "RST" resets counters, "STAT" replies with JSON. ---- */
static void udp_sink_task(void *arg)
{
    static uint8_t buf[1500];
    int s = udp_bind(UDP_SINK_PORT);
    if (s < 0) {
        ESP_LOGE(TAG, "udp sink bind failed");
        vTaskDelete(NULL);
    }
    uint32_t pkts = 0, max_seq = 0, reorder = 0, last_seq = 0;
    uint64_t bytes = 0;
    uint32_t t_first = 0, t_last = 0;

    for (;;) {
        struct sockaddr_in from;
        socklen_t fl = sizeof(from);
        int n = recvfrom(s, buf, sizeof(buf), 0, (struct sockaddr *)&from, &fl);
        if (n <= 0) {
            continue;
        }
        if (n == 3 && memcmp(buf, "RST", 3) == 0) {
            pkts = max_seq = reorder = last_seq = 0;
            bytes = 0;
            t_first = t_last = 0;
            sendto(s, "OK", 2, 0, (struct sockaddr *)&from, fl);
            continue;
        }
        if (n == 4 && memcmp(buf, "STAT", 4) == 0) {
            char out[200];
            uint32_t dur = t_last - t_first;
            int len = snprintf(out, sizeof(out),
                               "{\"pkts\":%" PRIu32 ",\"bytes\":%" PRIu64 ",\"max_seq\":%" PRIu32
                               ",\"reorder\":%" PRIu32 ",\"dur_ms\":%" PRIu32 "}",
                               pkts, bytes, max_seq, reorder, dur);
            sendto(s, out, len, 0, (struct sockaddr *)&from, fl);
            printf("METRIC {\"ev\":\"udp_sink\",\"t_ms\":%" PRIu32 ",%s\n", metrics_ms(), out + 1);
            continue;
        }
        if (n >= 4) {
            uint32_t seq = ((uint32_t)buf[0] << 24) | ((uint32_t)buf[1] << 16) | ((uint32_t)buf[2] << 8) | buf[3];
            if (pkts && seq < last_seq) {
                reorder++;
            }
            last_seq = seq;
            if (seq > max_seq) {
                max_seq = seq;
            }
        }
        uint32_t now = metrics_ms();
        if (!pkts) {
            t_first = now;
        }
        t_last = now;
        pkts++;
        bytes += n;
    }
}

/* ---- TCP sink (discard) ---- */
static void tcp_sink_task(void *arg)
{
    static uint8_t buf[2920];
    int ls = tcp_listen(TCP_SINK_PORT);
    if (ls < 0) {
        ESP_LOGE(TAG, "tcp sink listen failed");
        vTaskDelete(NULL);
    }
    for (;;) {
        int c = accept(ls, NULL, NULL);
        if (c < 0) {
            continue;
        }
        uint64_t bytes = 0;
        uint32_t t0 = metrics_ms();
        int n;
        while ((n = recv(c, buf, sizeof(buf), 0)) > 0) {
            bytes += n;
        }
        uint32_t dur = metrics_ms() - t0;
        close(c);
        printf("METRIC {\"ev\":\"tcp_sink\",\"t_ms\":%" PRIu32 ",\"bytes\":%" PRIu64 ",\"dur_ms\":%" PRIu32
               ",\"kbit_s\":%.1f}\n",
               metrics_ms(), bytes, dur, dur ? (double)bytes * 8.0 / dur : 0.0);
    }
}

/* ---- TCP source: send for N seconds, then close ---- */
static void tcp_source_task(void *arg)
{
    static uint8_t buf[1400];
    for (size_t i = 0; i < sizeof(buf); i++) {
        buf[i] = (uint8_t)i;
    }
    int ls = tcp_listen(TCP_SRC_PORT);
    if (ls < 0) {
        ESP_LOGE(TAG, "tcp source listen failed");
        vTaskDelete(NULL);
    }
    for (;;) {
        int c = accept(ls, NULL, NULL);
        if (c < 0) {
            continue;
        }
        uint64_t bytes = 0;
        uint32_t t0 = metrics_ms();
        const uint32_t limit = CONFIG_POC_BENCH_TCP_TX_SECONDS * 1000U;
        while (metrics_ms() - t0 < limit) {
            int n = send(c, buf, sizeof(buf), 0);
            if (n <= 0) {
                break;
            }
            bytes += n;
        }
        uint32_t dur = metrics_ms() - t0;
        shutdown(c, SHUT_RDWR);
        close(c);
        printf("METRIC {\"ev\":\"tcp_source\",\"t_ms\":%" PRIu32 ",\"bytes\":%" PRIu64 ",\"dur_ms\":%" PRIu32
               ",\"kbit_s\":%.1f}\n",
               metrics_ms(), bytes, dur, dur ? (double)bytes * 8.0 / dur : 0.0);
    }
}

void bench_start(void)
{
    /* Bench tasks run on core 0 so they compete with Wi-Fi, not with the
     * tcpip/WireGuard crypto pinned to core 1 - closer to a real app. */
    xTaskCreatePinnedToCore(udp_echo_task, "b_echo", 2560, NULL, 4, NULL, 0);
    xTaskCreatePinnedToCore(udp_sink_task, "b_usink", 3072, NULL, 4, NULL, 0);
    xTaskCreatePinnedToCore(tcp_sink_task, "b_tsink", 3072, NULL, 4, NULL, 0);
    xTaskCreatePinnedToCore(tcp_source_task, "b_tsrc", 3072, NULL, 4, NULL, 0);
}

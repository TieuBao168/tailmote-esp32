// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
#include "metrics.h"

#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
#include "sdkconfig.h"

#define MAX_TASKS 32

uint32_t metrics_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

metrics_heap_t metrics_heap_now(void)
{
    metrics_heap_t h = {
        .free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
        .largest_internal = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
    };
    return h;
}

void metrics_event(const char *ev, const char *extra_json)
{
    metrics_heap_t h = metrics_heap_now();
    printf("METRIC {\"ev\":\"%s\",\"t_ms\":%" PRIu32 ",\"heap\":%" PRIu32 ",\"largest\":%" PRIu32 "%s%s}\n",
           ev, metrics_ms(), h.free_internal, h.largest_internal,
           extra_json ? "," : "", extra_json ? extra_json : "");
}

void metrics_heap_delta(const char *what, metrics_heap_t before)
{
    metrics_heap_t now = metrics_heap_now();
    printf("METRIC {\"ev\":\"heap_delta\",\"what\":\"%s\",\"t_ms\":%" PRIu32 ",\"used\":%" PRId32
           ",\"heap\":%" PRIu32 ",\"largest\":%" PRIu32 "}\n",
           what, metrics_ms(), (int32_t)before.free_internal - (int32_t)now.free_internal,
           now.free_internal, now.largest_internal);
}

/* ---- periodic reporter ---- */

static TaskStatus_t s_tasks[MAX_TASKS];
static uint32_t s_prev_idle[portNUM_PROCESSORS];
static uint32_t s_prev_total;

static void report_cpu_and_heap(bool with_tasks)
{
    uint32_t total = 0;
    UBaseType_t n = uxTaskGetSystemState(s_tasks, MAX_TASKS, &total);

    uint32_t idle[portNUM_PROCESSORS] = {0};
    for (int core = 0; core < portNUM_PROCESSORS; core++) {
        TaskHandle_t ih = xTaskGetIdleTaskHandleForCore(core);
        for (UBaseType_t i = 0; i < n; i++) {
            if (s_tasks[i].xHandle == ih) {
                idle[core] = s_tasks[i].ulRunTimeCounter;
            }
        }
    }

    /* Run-time counter is per core on ESP-IDF; total is wall time of one core. */
    uint32_t dt = total - s_prev_total;
    float load[portNUM_PROCESSORS];
    for (int core = 0; core < portNUM_PROCESSORS; core++) {
        uint32_t di = idle[core] - s_prev_idle[core];
        load[core] = (s_prev_total == 0 || dt == 0) ? -1.0f : 100.0f * (1.0f - (float)di / (float)dt);
        if (load[core] < 0 && s_prev_total != 0) {
            load[core] = 0;
        }
        s_prev_idle[core] = idle[core];
    }
    s_prev_total = total;

    metrics_heap_t h = metrics_heap_now();
    printf("METRIC {\"ev\":\"periodic\",\"t_ms\":%" PRIu32 ",\"heap\":%" PRIu32 ",\"largest\":%" PRIu32
           ",\"heap_min\":%" PRIu32 ",\"cpu0\":%.1f,\"cpu1\":%.1f,\"tasks\":%u}\n",
           metrics_ms(), h.free_internal, h.largest_internal,
           (uint32_t)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
           load[0], portNUM_PROCESSORS > 1 ? load[1] : 0.0f, (unsigned)n);

    if (with_tasks) {
        for (UBaseType_t i = 0; i < n; i++) {
            /* On ESP-IDF, StackType_t is uint8_t: high-water mark is in bytes. */
            printf("METRIC {\"ev\":\"task\",\"t_ms\":%" PRIu32 ",\"name\":\"%s\",\"stack_free_min\":%u,\"core\":%d}\n",
                   metrics_ms(), s_tasks[i].pcTaskName, (unsigned)s_tasks[i].usStackHighWaterMark,
                   s_tasks[i].xCoreID == tskNO_AFFINITY ? -1 : (int)s_tasks[i].xCoreID);
        }
    }
}

static void metrics_task(void *arg)
{
    const uint32_t period = CONFIG_POC_METRICS_PERIOD_S;
    const uint32_t tasks_every = CONFIG_POC_TASKS_PERIOD_S / (period ? period : 1);
    uint32_t tick = 0;

    report_cpu_and_heap(false); /* prime counters */
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(period * 1000));
        tick++;
        report_cpu_and_heap(tasks_every && (tick % tasks_every) == 0);
    }
}

void metrics_start(void)
{
    xTaskCreatePinnedToCore(metrics_task, "metrics", 3072, NULL, 2, NULL, 0);
}

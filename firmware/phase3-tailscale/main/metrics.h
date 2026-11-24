// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
#pragma once

#include <stdint.h>

/*
 * Every measurement is printed as one line on the console:
 *   METRIC {"ev":"<name>", ...}
 * tools/bench/parse_metrics.py turns a captured log into CSV/JSON.
 */

/* Record a one-shot event (boot milestones, reconnects) with timestamp + heap. */
void metrics_event(const char *ev, const char *extra_json);

/* Heap snapshot helpers to measure the RAM cost of a block of code. */
typedef struct {
    uint32_t free_internal;
    uint32_t largest_internal;
} metrics_heap_t;

metrics_heap_t metrics_heap_now(void);
void metrics_heap_delta(const char *what, metrics_heap_t before);

/* Start the periodic reporter task (heap, CPU% per core, task stacks). */
void metrics_start(void);

/* Milliseconds since boot. */
uint32_t metrics_ms(void);

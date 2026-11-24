// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
/* [tailmote patch] In-place JSON pruning before cJSON_Parse(), see ml_json_prune.c. */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Overwrite (with spaces) every object member whose key is in the
 * NULL-terminated list `drop`, at any depth, together with its separating
 * comma. The text keeps its length and stays valid JSON. Returns the number
 * of bytes blanked. */
size_t ml_json_prune(char *json, size_t len, const char *const *drop);

/* In a MapResponse, blank every DERPMap.Regions member except the region IDs
 * in `keep` (0 entries ignored). MicroLink only connects to its home region;
 * the full map (28 regions, ~16 KB) is most of a netmap. If none of `keep` is
 * present, nothing is touched so MicroLink's "first usable region" fallback
 * (custom control servers) still sees every region. Returns bytes blanked. */
size_t ml_json_prune_derp_regions(char *json, size_t len, const unsigned *keep, size_t nkeep);

/* Keys of MapResponse / long-poll messages that MicroLink does not read. */
extern const char *const ML_JSON_PRUNE_MAP_KEYS[];

#ifdef __cplusplus
}
#endif

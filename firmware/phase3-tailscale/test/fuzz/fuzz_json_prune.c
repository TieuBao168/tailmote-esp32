// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
/*
 * Fuzz target for components/microlink/src/ml_json_prune.c (tailmote patch).
 *
 * libFuzzer entry point. Build either way:
 *   clang -g -fsanitize=fuzzer,address,undefined -I<ml>/include -I<cjson> \
 *         fuzz_json_prune.c <ml>/src/ml_json_prune.c <cjson>/cJSON.c
 *   gcc (no sanitizers): add driver.c, which mutates inputs and puts each one
 *         right before a guard page so any read past the end faults.
 *
 * Oracles (abort() on violation):
 *   0. no read/write outside the buffer (ASan, or driver.c's guard pages);
 *   1. every changed byte became ' ', pruning is idempotent;
 *   2. valid JSON stays valid JSON;
 *   3. for valid JSON without '\' and with depth <= 40: the pruned tree equals
 *      the input tree minus every dropped member and every DERP region
 *      other than the kept ones (when one of them is present).
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "ml_json_prune.h"

static const unsigned KEEP[] = {9, 0};

static void fail(const char *why, const uint8_t *data, size_t size)
{
    fprintf(stderr, "ORACLE VIOLATION: %s (input %zu bytes)\n", why, size);
    FILE *f = fopen("crash-oracle.json", "wb");
    if (f) {
        fwrite(data, 1, size, f);
        fclose(f);
    }
    abort();
}

static int max_depth(const char *s, size_t n)
{
    int d = 0, m = 0, in_str = 0;
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (in_str) {
            if (c == '\\') {
                i++;
            } else if (c == '"') {
                in_str = 0;
            }
        } else if (c == '"') {
            in_str = 1;
        } else if (c == '{' || c == '[') {
            if (++d > m) {
                m = d;
            }
        } else if (c == '}' || c == ']') {
            d--;
        }
    }
    return m;
}

static int dropped(const char *key)
{
    for (const char *const *k = ML_JSON_PRUNE_MAP_KEYS; *k; k++) {
        if (strcmp(*k, key) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Reference: delete dropped members at every depth. */
static void ref_drop_keys(cJSON *node)
{
    cJSON *c = node ? node->child : NULL;
    while (c) {
        cJSON *next = c->next;
        if (cJSON_IsObject(node) && c->string && dropped(c->string)) {
            cJSON_Delete(cJSON_DetachItemViaPointer(node, c));
        } else {
            ref_drop_keys(c);
        }
        c = next;
    }
}

/* Same region-id rule as ml_json_prune.c: all digits, unsigned arithmetic. */
static unsigned ref_region_id(const char *k)
{
    unsigned id = 0;
    if (!*k) {
        return 0;
    }
    for (; *k; k++) {
        if (*k < '0' || *k > '9') {
            return 0;
        }
        id = id * 10 + (unsigned)(*k - '0');
    }
    return id;
}

static int ref_kept(const char *k)
{
    unsigned id = ref_region_id(k);
    return id && (id == KEEP[0] || id == KEEP[1]);
}

/* Reference: in the top-level object's first "DERPMap" member (an object) and
 * its first "Regions" member (an object), keep only the kept regions, if any
 * kept region is present. First member, because the pruner stops at the first
 * matching key (find_member). */
static void ref_drop_regions(cJSON *root)
{
    if (!cJSON_IsObject(root)) {
        return;
    }
    cJSON *dm = NULL, *regions = NULL;
    for (cJSON *c = root->child; c; c = c->next) {
        if (c->string && strcmp(c->string, "DERPMap") == 0) {
            dm = c;
            break;
        }
    }
    if (!cJSON_IsObject(dm)) {
        return;
    }
    for (cJSON *c = dm->child; c; c = c->next) {
        if (c->string && strcmp(c->string, "Regions") == 0) {
            regions = c;
            break;
        }
    }
    if (!cJSON_IsObject(regions)) {
        return;
    }
    int any = 0;
    for (cJSON *c = regions->child; c; c = c->next) {
        any |= ref_kept(c->string);
    }
    if (!any) {
        return;
    }
    cJSON *c = regions->child;
    while (c) {
        cJSON *next = c->next;
        if (!ref_kept(c->string)) {
            cJSON_Delete(cJSON_DetachItemViaPointer(regions, c));
        }
        c = next;
    }
}

/* Working buffer for the pruners. libFuzzer builds use malloc (ASan checks
 * it); driver.c (built with -DFUZZ_GUARDED_ALLOC) provides them to place the buffer right before a guard page
 * (and, every other run, right after one). */
#ifdef FUZZ_GUARDED_ALLOC
char *fuzz_work_alloc(size_t n);
void fuzz_work_free(char *p, size_t n);
#else
static char *fuzz_work_alloc(size_t n) { return malloc(n ? n : 1); }
static void fuzz_work_free(char *p, size_t n) { (void)n; free(p); }
#endif

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    /* Validity of the input as one complete JSON value. */
    const char *end = NULL;
    cJSON *in = (memchr(data, 0, size) == NULL)
                    ? cJSON_ParseWithLengthOpts((const char *)data, size, &end, 0)
                    : NULL;
    int valid = in && end == (const char *)data + size;
    if (!valid) {
        cJSON_Delete(in);
        in = NULL;
    }

    char *buf = fuzz_work_alloc(size);
    if (!buf) {
        cJSON_Delete(in);
        return 0;
    }
    memcpy(buf, data, size);
    /* same order as prune_map() in ml_coord.c */
    ml_json_prune_derp_regions(buf, size, KEEP, 2);
    ml_json_prune(buf, size, ML_JSON_PRUNE_MAP_KEYS);

    for (size_t i = 0; i < size; i++) {
        if (buf[i] != (char)data[i] && buf[i] != ' ') {
            fail("byte changed to something other than a space", data, size);
        }
    }
    char *again = fuzz_work_alloc(size);
    memcpy(again, buf, size);
    /* same order as prune_map() in ml_coord.c */
    ml_json_prune_derp_regions(again, size, KEEP, 2);
    ml_json_prune(again, size, ML_JSON_PRUNE_MAP_KEYS);
    if (memcmp(again, buf, size) != 0) {
        fail("not idempotent", data, size);
    }
    fuzz_work_free(again, size);

    if (valid) {
        const char *oend = NULL;
        cJSON *out = cJSON_ParseWithLengthOpts(buf, size, &oend, 0);
        if (!out || oend != buf + size) {
            fail("valid JSON became invalid", data, size);
        }
        /* cJSON is laxer than RFC 8259 (any byte <= 0x20 is whitespace); the
         * pruner follows the RFC and leaves such input alone, so only compare
         * when there is no other control byte. */
        int rfc_ws = 1;
        for (size_t i = 0; i < size; i++) {
            if (data[i] < 0x20 && data[i] != '\t' && data[i] != '\n' && data[i] != '\r') {
                rfc_ws = 0;
                break;
            }
        }
        if (rfc_ws && !memchr(data, '\\', size) && max_depth((const char *)data, size) <= 40) {
            ref_drop_keys(in);
            ref_drop_regions(in);
            /* Compare the serialised trees, not cJSON_Compare(): that looks
             * members up by name, so objects with duplicate keys never compare
             * equal. Printing keeps member order and duplicates. */
            char *want = cJSON_PrintUnformatted(in), *got = cJSON_PrintUnformatted(out);
            if (!want || !got || strcmp(want, got) != 0) {
                fprintf(stderr, "want: %.300s\ngot:  %.300s\n", want ? want : "?", got ? got : "?");
                fail("pruned tree differs from reference", data, size);
            }
            cJSON_free(want);
            cJSON_free(got);
        }
        cJSON_Delete(out);
    }
    cJSON_Delete(in);
    fuzz_work_free(buf, size);
    return 0;
}

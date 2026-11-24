// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
/*
 * [tailmote patch] In-place JSON pruning before cJSON_Parse().
 *
 * On ESP32 without PSRAM a 21 KB MapResponse could not be parsed once the
 * DERP TLS session was up: cJSON builds a DOM of 2-3x the text size and the
 * heap ran out (docs/03 section 7). Most of that text is members MicroLink
 * never reads (per-peer Hostinfo, UserProfiles, DNSConfig, CapMap, ...).
 *
 * ml_json_prune() overwrites every object member whose key is in the drop
 * list (at any depth), plus the comma that separated it, with spaces. The
 * result is still valid JSON of the same length, so the buffer and the
 * callers' offsets stay as they are; cJSON skips whitespace for free.
 *
 * Malformed input (as far as this scanner can tell) is left untouched; the
 * caller's cJSON_Parse then reports it as before.
 */
#include "ml_json_prune.h"

#include <string.h>

#define PRUNE_MAX_DEPTH 48

/* Index just past the string that starts at s[i] == '"', or 0 if unterminated. */
static size_t skip_string(const char *s, size_t n, size_t i)
{
    for (i++; i < n; i++) {
        if (s[i] == '\\') {
            i++;
        } else if (s[i] == '"') {
            return i + 1;
        }
    }
    return 0;
}

static size_t skip_ws(const char *s, size_t n, size_t i)
{
    while (i < n && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) {
        i++;
    }
    return i;
}

/* Index just past a literal (true / false / null / number characters), or 0. */
static size_t skip_literal(const char *s, size_t n, size_t i)
{
    static const char *const WORDS[] = {"true", "false", "null"};
    for (size_t w = 0; w < 3; w++) {
        size_t l = strlen(WORDS[w]);
        if (i + l <= n && memcmp(s + i, WORDS[w], l) == 0) {
            return i + l;
        }
    }
    size_t start = i;
    while (i < n && ((s[i] >= '0' && s[i] <= '9') || s[i] == '-' || s[i] == '+' ||
                     s[i] == '.' || s[i] == 'e' || s[i] == 'E')) {
        i++;
    }
    return i > start ? i : 0;
}

/* Index just past the JSON value that starts at s[i], or 0 on error. */
static size_t skip_value(const char *s, size_t n, size_t i)
{
    if (i >= n) {
        return 0;
    }
    if (s[i] == '"') {
        return skip_string(s, n, i);
    }
    if (s[i] == '{' || s[i] == '[') {
        int depth = 0;
        while (i < n) {
            char c = s[i];
            if (c == '"') {
                i = skip_string(s, n, i);
                if (!i) {
                    return 0;
                }
                continue;
            }
            if (c == '{' || c == '[') {
                depth++;
            } else if (c == '}' || c == ']') {
                if (--depth == 0) {
                    return i + 1;
                }
            }
            i++;
        }
        return 0;
    }
    return skip_literal(s, n, i);
}

/* Strict JSON syntax check of the whole buffer (one value, optional
 * whitespace around it), constant memory, nesting <= PRUNE_MAX_DEPTH. Number
 * spelling is not checked (only its characters); strings are only checked for
 * termination. Both pruners run only when this passes: on well-formed JSON
 * blanking whole members keeps it well formed, which makes pruning
 * idempotent. The tolerant scanner it replaced let one pass turn malformed
 * input into differently malformed input that the next pass pruned further
 * (four cases found by the fuzzer: stray '"', missing / doubled commas). */
static bool json_valid(const char *s, size_t n)
{
    enum { VALUE, VALUE_OR_END, KEY, KEY_OR_END, AFTER_VALUE } state = VALUE;
    bool is_obj[PRUNE_MAX_DEPTH];
    int top = -1;
    size_t i = 0;

    for (;;) {
        i = skip_ws(s, n, i);
        if (state == AFTER_VALUE && top < 0) {
            return i == n;
        }
        if (i >= n) {
            return false;
        }
        char c = s[i];
        switch (state) {
        case VALUE_OR_END:
            if (c == ']') {
                top--;
                i++;
                state = AFTER_VALUE;
                break;
            }
            /* fall through */
        case VALUE:
            if (c == '{' || c == '[') {
                if (++top >= PRUNE_MAX_DEPTH) {
                    return false;
                }
                is_obj[top] = (c == '{');
                state = (c == '{') ? KEY_OR_END : VALUE_OR_END;
                i++;
            } else {
                size_t e = (c == '"') ? skip_string(s, n, i) : skip_literal(s, n, i);
                if (!e) {
                    return false;
                }
                i = e;
                state = AFTER_VALUE;
            }
            break;
        case KEY_OR_END:
            if (c == '}') {
                top--;
                i++;
                state = AFTER_VALUE;
                break;
            }
            /* fall through */
        case KEY: {
            if (c != '"') {
                return false;
            }
            size_t e = skip_string(s, n, i);
            if (!e) {
                return false;
            }
            i = skip_ws(s, n, e);
            if (i >= n || s[i] != ':') {
                return false;
            }
            i++;
            state = VALUE;
            break;
        }
        case AFTER_VALUE:
            if (c == ',') {
                state = is_obj[top] ? KEY : VALUE;
                i++;
            } else if (c == (is_obj[top] ? '}' : ']')) {
                top--;
                i++;
            } else {
                return false;
            }
            break;
        }
    }
}

static bool key_dropped(const char *key, size_t len, const char *const *drop)
{
    for (; *drop; drop++) {
        if (strlen(*drop) == len && memcmp(*drop, key, len) == 0) {
            return true;
        }
    }
    return false;
}

/* One pass over the document. With write=false nothing is modified but the
 * traversal is identical (dropped values are skipped the same way), so a dry
 * run fails exactly where a writing run would stop. Returns the bytes blanked,
 * or (size_t)-1 on a structural error (unterminated string, missing ':',
 * unbalanced brackets, nesting deeper than PRUNE_MAX_DEPTH, trailing open
 * container). */
static size_t prune_pass(char *s, size_t n, const char *const *drop, bool write)
{
    /* Per open container: is it an object, does the next string start a
     * member, and where is the comma before the current member (to blank
     * it when the dropped member is the last one). */
    bool is_obj[PRUNE_MAX_DEPTH];
    bool want_key[PRUNE_MAX_DEPTH];
    size_t prev_comma[PRUNE_MAX_DEPTH];
    int top = -1;
    size_t blanked = 0;
    const size_t NONE = (size_t)-1, ERR = (size_t)-1;

    for (size_t i = 0; i < n;) {
        char c = s[i];
        if (c == '{' || c == '[') {
            if (++top >= PRUNE_MAX_DEPTH) {
                return ERR;
            }
            is_obj[top] = (c == '{');
            want_key[top] = (c == '{');
            prev_comma[top] = NONE;
            i++;
        } else if (c == '}' || c == ']') {
            if (top < 0 || is_obj[top] != (c == '}')) {
                return ERR;
            }
            top--;
            i++;
        } else if (c == ',') {
            if (top >= 0 && is_obj[top]) {
                want_key[top] = true;
                prev_comma[top] = i;
            }
            i++;
        } else if (c == '"') {
            size_t end = skip_string(s, n, i);
            if (!end) {
                return ERR;
            }
            if (top >= 0 && is_obj[top] && want_key[top]) {
                want_key[top] = false;
                if (key_dropped(s + i + 1, end - i - 2, drop)) {
                    size_t p = skip_ws(s, n, end);
                    if (p >= n || s[p] != ':') {
                        return ERR;
                    }
                    size_t v = skip_value(s, n, skip_ws(s, n, p + 1));
                    if (!v) {
                        return ERR;
                    }
                    if (write) {
                        memset(s + i, ' ', v - i);
                    }
                    blanked += v - i;
                    size_t after = skip_ws(s, n, v);
                    if (after >= n || (s[after] != ',' && s[after] != '}')) {
                        /* Missing comma: the next string would be read as a
                         * value now but as a key once this member is blank,
                         * so a second pass would prune more (fuzzer). */
                        return ERR;
                    }
                    if (s[after] == ',') {
                        if (write) {
                            s[after] = ' ';        /* "k":v,  next member follows */
                        }
                        blanked++;
                        want_key[top] = true;
                        after++;
                    } else if (prev_comma[top] != NONE) {
                        if (write) {
                            s[prev_comma[top]] = ' ';  /* last member: drop the comma before it */
                        }
                        blanked++;
                        prev_comma[top] = NONE;
                    }
                    i = after;
                    continue;
                }
            }
            i = end;
        } else {
            i++;
        }
    }
    return top == -1 ? blanked : ERR;
}

size_t ml_json_prune(char *s, size_t n, const char *const *drop)
{
    if (!s || !drop) {
        return 0;
    }
    /* All or nothing: prune only well-formed JSON, so malformed input
     * reaches cJSON_Parse() untouched and pruning is idempotent. */
    if (!json_valid(s, n)) {
        return 0;
    }
    size_t r = prune_pass(s, n, drop, true);
    return r == (size_t)-1 ? 0 : r;
}

/* Value index of member `key` of the object whose '{' is at s[obj], or 0. */
static size_t find_member(const char *s, size_t n, size_t obj, const char *key)
{
    size_t klen = strlen(key);
    size_t i = skip_ws(s, n, obj + 1);
    while (i < n && s[i] == '"') {
        size_t kend = skip_string(s, n, i);
        if (!kend) {
            return 0;
        }
        size_t p = skip_ws(s, n, kend);
        if (p >= n || s[p] != ':') {
            return 0;
        }
        size_t v = skip_ws(s, n, p + 1);
        if (kend - i - 2 == klen && memcmp(s + i + 1, key, klen) == 0) {
            /* Truncated after the ':' -> no value; callers read s[v]. (Found
             * by the fuzzer: `{"DERPMap":` read one byte past the buffer.) */
            return v < n ? v : 0;
        }
        size_t vend = skip_value(s, n, v);
        if (!vend) {
            return 0;
        }
        i = skip_ws(s, n, vend);
        if (i < n && s[i] == ',') {
            i = skip_ws(s, n, i + 1);
        }
    }
    return 0;
}

/* Region ID of the member key at s[i] (a JSON string of digits), 0 if not numeric. */
static unsigned region_id(const char *s, size_t i, size_t kend)
{
    unsigned id = 0;
    if (kend - i <= 2) {
        return 0;
    }
    for (size_t k = i + 1; k < kend - 1; k++) {
        if (s[k] < '0' || s[k] > '9') {
            return 0;
        }
        id = id * 10 + (unsigned)(s[k] - '0');
    }
    return id;
}

static bool region_kept(unsigned id, const unsigned *keep, size_t nkeep)
{
    for (size_t k = 0; id && k < nkeep; k++) {
        if (keep[k] == id) {
            return true;
        }
    }
    return false;
}

/* Walk the members of the object at s[obj]. Pass 1 (blank=false) only checks
 * the object is well formed and whether a kept region is present (returns
 * 1/0, or (size_t)-1 if malformed). Pass 2 blanks the other members and
 * returns the bytes blanked. Constant memory: runs on the ml_coord task stack. */
static size_t walk_regions(char *s, size_t n, size_t obj, const unsigned *keep, size_t nkeep, bool blank)
{
    const size_t NONE = (size_t)-1;
    size_t blanked = 0, last_kept_comma = NONE;
    bool found = false;
    size_t i = skip_ws(s, n, obj + 1);
    while (i < n && s[i] == '"') {
        size_t kend = skip_string(s, n, i);
        if (!kend) {
            return NONE;
        }
        size_t p = skip_ws(s, n, kend);
        if (p >= n || s[p] != ':') {
            return NONE;
        }
        size_t ve = skip_value(s, n, skip_ws(s, n, p + 1));
        if (!ve) {
            return NONE;
        }
        size_t after = skip_ws(s, n, ve);
        size_t comma = (after < n && s[after] == ',') ? after : NONE;
        bool kept = region_kept(region_id(s, i, kend), keep, nkeep);
        found |= kept;
        if (blank) {
            if (kept) {
                last_kept_comma = comma;
            } else {
                memset(s + i, ' ', ve - i);
                blanked += ve - i;
                if (comma != NONE) {
                    s[comma] = ' ';
                    blanked++;
                }
            }
        }
        i = (comma != NONE) ? skip_ws(s, n, comma + 1) : after;
    }
    if (i >= n || s[i] != '}') {
        return NONE;
    }
    if (!blank) {
        return found ? 1 : 0;
    }
    /* A kept member followed only by blanked ones still has its comma. */
    if (last_kept_comma != NONE && s[last_kept_comma] == ',') {
        size_t j = skip_ws(s, n, last_kept_comma + 1);
        if (j < n && s[j] == '}') {
            s[last_kept_comma] = ' ';
            blanked++;
        }
    }
    return blanked;
}

size_t ml_json_prune_derp_regions(char *s, size_t n, const unsigned *keep, size_t nkeep)
{
    if (!s || !keep || !nkeep || !json_valid(s, n)) {
        return 0;   /* malformed: leave it to cJSON_Parse() (see json_valid) */
    }
    size_t top = skip_ws(s, n, 0);
    if (top >= n || s[top] != '{') {
        return 0;
    }
    size_t dm = find_member(s, n, top, "DERPMap");
    if (!dm || s[dm] != '{') {
        return 0;
    }
    size_t regions = find_member(s, n, dm, "Regions");
    if (!regions || s[regions] != '{') {
        return 0;
    }
    /* Home region absent (e.g. a custom control server's own DERP map) or
     * malformed: keep everything for MicroLink's fallback logic. */
    if (walk_regions(s, n, regions, keep, nkeep, false) != 1) {
        return 0;
    }
    return walk_regions(s, n, regions, keep, nkeep, true);
}

/* Members of control-plane responses that MicroLink never reads (checked
 * against every cJSON_GetObjectItem in src/, docs/03 section 7). */
const char *const ML_JSON_PRUNE_MAP_KEYS[] = {
    "Hostinfo", "UserProfiles", "DNSConfig", "SSHPolicy", "CapMap", "Capabilities",
    "ControlDialPlan", "CollectServices", "DisplayMessages", "ClientVersion",
    "TKAInfo", "DefaultAutoUpdate", "Debug",
    NULL,
};

// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
/*
 * Standalone fuzz driver for hosts without libFuzzer/ASan (e.g. MinGW gcc).
 *
 *   driver [-n ITER] [-t SECONDS] [-s SEED] [corpus files...]
 *
 * Each iteration feeds LLVMFuzzerTestOneInput() either a mutated corpus entry
 * or a freshly generated, MapResponse-shaped valid JSON document. Working
 * buffers come from fuzz_work_alloc(): placed directly before an inaccessible
 * page (and, every other allocation, directly after one), so a one-byte
 * overread or overwrite faults immediately. On a fault or abort the input is
 * written to crash-<iteration>.json.
 *
 * Build: gcc -O1 -g -DFUZZ_GUARDED_ALLOC driver.c fuzz_json_prune.c \
 *        <ml>/src/ml_json_prune.c <cjson>/cJSON.c -I<ml>/include -I<cjson>
 */
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#include <unistd.h>
#endif

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

#define MAX_INPUT (64 * 1024)
#define MAX_CORPUS 256

/* ------------------------------------------------------------ guard pages -- */
static size_t page_size(void)
{
#ifdef _WIN32
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    return si.dwPageSize;
#else
    return (size_t)sysconf(_SC_PAGESIZE);
#endif
}

typedef struct {
    char *base;
    size_t total;
} region_t;

#define MAX_LIVE 4
static struct {
    char *user;
    region_t r;
} s_live[MAX_LIVE];
static unsigned s_flip;

char *fuzz_work_alloc(size_t n)
{
    size_t ps = page_size();
    size_t body = ((n ? n : 1) + ps - 1) / ps * ps;
    size_t total = body + 2 * ps;
#ifdef _WIN32
    char *base = VirtualAlloc(NULL, total, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!base) {
        return NULL;
    }
    DWORD old;
    VirtualProtect(base, ps, PAGE_NOACCESS, &old);
    VirtualProtect(base + ps + body, ps, PAGE_NOACCESS, &old);
#else
    char *base = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (base == MAP_FAILED) {
        return NULL;
    }
    mprotect(base, ps, PROT_NONE);
    mprotect(base + ps + body, ps, PROT_NONE);
#endif
    /* Alternate: end of buffer against the trailing guard (catches overreads,
     * the common bug), or start against the leading guard (underreads). */
    char *user = (s_flip++ & 1) ? base + ps : base + ps + body - n;
    for (int i = 0; i < MAX_LIVE; i++) {
        if (!s_live[i].user) {
            s_live[i].user = user;
            s_live[i].r.base = base;
            s_live[i].r.total = total;
            return user;
        }
    }
    abort(); /* more than MAX_LIVE buffers alive: harness bug */
}

void fuzz_work_free(char *p, size_t n)
{
    (void)n;
    for (int i = 0; i < MAX_LIVE; i++) {
        if (s_live[i].user == p) {
#ifdef _WIN32
            VirtualFree(s_live[i].r.base, 0, MEM_RELEASE);
#else
            munmap(s_live[i].r.base, s_live[i].r.total);
#endif
            s_live[i].user = NULL;
            return;
        }
    }
}

/* ------------------------------------------------------------ crash dump -- */
static const uint8_t *s_cur;
static size_t s_cur_len;
static unsigned long long s_iter;

static void dump_current(const char *why)
{
    char name[64];
    snprintf(name, sizeof(name), "crash-%llu.json", s_iter);
    FILE *f = fopen(name, "wb");
    if (f) {
        fwrite(s_cur, 1, s_cur_len, f);
        fclose(f);
    }
    fprintf(stderr, "\nCRASH (%s) at iteration %llu, input %zu bytes -> %s\n", why, s_iter, s_cur_len, name);
}

#ifdef _WIN32
static LONG WINAPI on_exception(EXCEPTION_POINTERS *ep)
{
    dump_current(ep->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION
                     ? "access violation" : "exception");
    return EXCEPTION_EXECUTE_HANDLER;
}
#endif

static void on_signal(int sig)
{
    dump_current(sig == SIGABRT ? "abort / oracle" : "signal");
    signal(sig, SIG_DFL);
    raise(sig);
}

/* ----------------------------------------------------------------- random -- */
static uint64_t s_rng;
static uint32_t rnd(void)
{
    s_rng ^= s_rng << 13;
    s_rng ^= s_rng >> 7;
    s_rng ^= s_rng << 17;
    return (uint32_t)(s_rng >> 16);
}
static uint32_t below(uint32_t n) { return n ? rnd() % n : 0; }

/* ------------------------------------------------- structured generation -- */
static const char *const KEYS[] = {
    "Hostinfo", "UserProfiles", "DNSConfig", "CapMap", "Debug", "TKAInfo",      /* dropped */
    "DERPMap", "Regions", "Nodes", "Peers", "Node", "Key", "ID", "Endpoints",     /* read */
    "HostName", "RegionID", "PacketFilter", "Addresses", "Name", "x", "",
    "9", "09", "1", "10", "19", "4294967305", "a9",                                /* region ids */
};
#define NKEYS (sizeof(KEYS) / sizeof(KEYS[0]))

typedef struct {
    char *b;
    size_t n, cap;
} sbuf;

static void put(sbuf *s, const char *t)
{
    size_t l = strlen(t);
    if (s->n + l + 1 > s->cap) {
        return; /* stop growing at the cap; output may be invalid, that's fine */
    }
    memcpy(s->b + s->n, t, l);
    s->n += l;
}

static void ws(sbuf *s)
{
    static const char *const W[] = {"", "", "", " ", "\n", "\t", "  \r\n "};
    put(s, W[below(7)]);
}

static void gen_value(sbuf *s, int depth);

static void gen_object(sbuf *s, int depth, int regions)
{
    put(s, "{");
    int n = (int)below(depth > 6 ? 2 : 6);
    for (int i = 0; i < n; i++) {
        if (i) {
            put(s, ",");
        }
        ws(s);
        put(s, "\"");
        if (regions) {
            put(s, KEYS[21 + below(7)]);       /* region-id-like keys */
        } else {
            put(s, KEYS[below(NKEYS)]);
        }
        put(s, "\"");
        ws(s);
        put(s, ":");
        ws(s);
        if (!regions && below(4) == 0) {
            put(s, "{\"Regions\":");
            gen_object(s, depth + 2, 1);
            put(s, "}");
        } else {
            gen_value(s, depth + 1);
        }
        ws(s);
    }
    put(s, "}");
}

static void gen_value(sbuf *s, int depth)
{
    static const char *const LIT[] = {
        "0", "-1.5e3", "true", "false", "null", "\"\"", "\"str\"", "\"}]\\\"\"", "\"a,b:c\"",
        "\"9\"", "\"\\u0041\"", "12345678901234567890",
    };
    uint32_t r = below(depth > 8 ? 2 : 10);
    if (r < 3) {
        put(s, LIT[below(sizeof(LIT) / sizeof(LIT[0]))]);
    } else if (r < 6) {
        put(s, "[");
        int n = (int)below(5);
        for (int i = 0; i < n; i++) {
            if (i) {
                put(s, ",");
            }
            ws(s);
            gen_value(s, depth + 1);
            ws(s);
        }
        put(s, "]");
    } else {
        gen_object(s, depth, 0);
    }
}

static size_t generate(char *out, size_t cap)
{
    sbuf s = {out, 0, cap};
    ws(&s);
    if (below(3)) {
        /* MapResponse-like top level with a DERPMap */
        put(&s, "{\"Node\":");
        gen_value(&s, 1);
        put(&s, ",\"DERPMap\":{\"Regions\":");
        gen_object(&s, 2, 1);
        put(&s, below(2) ? ",\"OmitDefaultRegions\":false}" : "}");
        put(&s, ",\"Peers\":");
        gen_value(&s, 1);
        if (below(2)) {
            put(&s, ",\"DERPMap\":{\"Regions\":{\"1\":{}}}"); /* duplicate key */
        }
        put(&s, "}");
    } else {
        gen_value(&s, 0);
    }
    ws(&s);
    return s.n;
}

/* --------------------------------------------------------------- mutation -- */
static const char *const TOKENS[] = {
    "{", "}", "[", "]", "\"", "\\", ":", ",", " ", "\"Hostinfo\":", "\"DERPMap\":",
    "\"Regions\":", "\"9\":", "{}", "[]", "\"\\\"", "null", "\\u00",
};

static size_t mutate(uint8_t *d, size_t n, size_t cap, uint8_t *const *corpus, const size_t *clen, int nc)
{
    int ops = 1 + (int)below(6);
    for (int k = 0; k < ops; k++) {
        uint32_t op = below(7);
        if (op == 0 && n) {                       /* flip a bit */
            d[below((uint32_t)n)] ^= (uint8_t)(1u << below(8));
        } else if (op == 1 && n) {                /* interesting byte */
            static const char I[] = "{}[]\":,\\ 0a9\n";
            d[below((uint32_t)n)] = (uint8_t)I[below(sizeof(I) - 1)];
        } else if (op == 2) {                     /* insert token */
            const char *t = TOKENS[below(sizeof(TOKENS) / sizeof(TOKENS[0]))];
            size_t l = strlen(t), at = below((uint32_t)n + 1);
            if (n + l <= cap) {
                memmove(d + at + l, d + at, n - at);
                memcpy(d + at, t, l);
                n += l;
            }
        } else if (op == 3 && n) {                /* delete a range */
            size_t at = below((uint32_t)n), l = 1 + below(16);
            if (at + l > n) {
                l = n - at;
            }
            memmove(d + at, d + at + l, n - at - l);
            n -= l;
        } else if (op == 4 && n) {                /* duplicate a range */
            size_t at = below((uint32_t)n), l = 1 + below(64);
            if (at + l > n) {
                l = n - at;
            }
            if (n + l <= cap) {
                size_t to = below((uint32_t)n + 1);
                memmove(d + to + l, d + to, n - to);
                memcpy(d + to, d + (at < to ? at : at + l), l);
                n += l;
            }
        } else if (op == 5 && n) {                /* truncate */
            n = below((uint32_t)n);
        } else if (op == 6 && nc) {               /* splice another corpus entry */
            int j = (int)below((uint32_t)nc);
            size_t a = below((uint32_t)n + 1), b = below((uint32_t)clen[j] + 1);
            size_t l = clen[j] - b;
            if (a + l > cap) {
                l = cap - a;
            }
            memcpy(d + a, corpus[j] + b, l);
            n = a + l;
        }
    }
    return n;
}

static int load(const char *path, uint8_t **out, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        return 0;
    }
    *out = malloc(MAX_INPUT);
    *len = fread(*out, 1, MAX_INPUT, f);
    fclose(f);
    return 1;
}

int main(int argc, char **argv)
{
    unsigned long long iters = 100000;
    double seconds = 0;
    uint64_t seed = (uint64_t)time(NULL);
    uint8_t *corpus[MAX_CORPUS];
    size_t clen[MAX_CORPUS];
    int nc = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-n") && i + 1 < argc) {
            iters = strtoull(argv[++i], NULL, 10);
        } else if (!strcmp(argv[i], "-t") && i + 1 < argc) {
            seconds = atof(argv[++i]);
            iters = ~0ULL;
        } else if (!strcmp(argv[i], "-s") && i + 1 < argc) {
            seed = strtoull(argv[++i], NULL, 10);
        } else if (nc < MAX_CORPUS && load(argv[i], &corpus[nc], &clen[nc])) {
            nc++;
        }
    }
    s_rng = seed ? seed : 0x9e3779b97f4a7c15ULL;
    printf("fuzz: seed=%llu corpus=%d %s\n", (unsigned long long)seed, nc,
           seconds > 0 ? "time-limited" : "iteration-limited");

#ifdef _WIN32
    SetUnhandledExceptionFilter(on_exception);
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
#else
    signal(SIGSEGV, on_signal);
    signal(SIGBUS, on_signal);
#endif
    signal(SIGABRT, on_signal);

    uint8_t *in = malloc(MAX_INPUT);
    clock_t t0 = clock();
    unsigned long long generated = 0;
    for (s_iter = 0; s_iter < iters; s_iter++) {
        size_t n;
        if (!nc || below(2)) {
            n = generate((char *)in, MAX_INPUT);
            generated++;
            if (below(3) == 0) {
                n = mutate(in, n, MAX_INPUT, corpus, clen, nc);
            }
        } else {
            int j = (int)below((uint32_t)nc);
            memcpy(in, corpus[j], clen[j]);
            n = mutate(in, clen[j], MAX_INPUT, corpus, clen, nc);
        }
        s_cur = in;
        s_cur_len = n;
        LLVMFuzzerTestOneInput(in, n);
        if (seconds > 0 && (s_iter & 255) == 0 &&
            (double)(clock() - t0) / CLOCKS_PER_SEC >= seconds) {
            s_iter++;
            break;
        }
    }
    double el = (double)(clock() - t0) / CLOCKS_PER_SEC;
    printf("fuzz: %llu inputs (%llu generated) in %.1f s, 0 failures\n", s_iter, generated, el);
    return 0;
}

// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
/*
 * Host test for components/microlink/src/ml_json_prune.c.
 *   gcc -I../components/microlink/include test_json_prune.c ../components/microlink/src/ml_json_prune.c
 * Run by `tm test` (tests/test_json_prune_c.py) when gcc is available.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ml_json_prune.h"

static int failures;

/* Remove whitespace outside strings, so expectations stay readable. */
static void squeeze(const char *in, char *out)
{
    int in_str = 0;
    for (; *in; in++) {
        if (in_str) {
            *out++ = *in;
            if (*in == '\\' && in[1]) {
                *out++ = *++in;
            } else if (*in == '"') {
                in_str = 0;
            }
        } else if (*in == '"') {
            in_str = 1;
            *out++ = *in;
        } else if (*in != ' ' && *in != '\n' && *in != '\t' && *in != '\r') {
            *out++ = *in;
        }
    }
    *out = 0;
}

static void check(const char *name, const char *in, const char *const *drop, const char *want)
{
    size_t n = strlen(in);
    char *buf = malloc(n + 1), *got = malloc(n + 1), *exp = malloc(strlen(want) + 1);
    memcpy(buf, in, n + 1);
    ml_json_prune(buf, n, drop);
    if (strlen(buf) != n) {
        printf("FAIL %s: length changed\n", name);
        failures++;
    }
    squeeze(buf, got);
    squeeze(want, exp);
    if (strcmp(got, exp) != 0) {
        printf("FAIL %s\n  got:  %s\n  want: %s\n", name, got, exp);
        failures++;
    } else {
        printf("ok   %s\n", name);
    }
    free(buf);
    free(got);
    free(exp);
}

static void check_regions(const char *name, const char *in, const unsigned *keep, size_t nkeep,
                          const char *want)
{
    size_t n = strlen(in);
    char *buf = malloc(n + 1), *got = malloc(n + 1), *exp = malloc(strlen(want) + 1);
    memcpy(buf, in, n + 1);
    ml_json_prune_derp_regions(buf, n, keep, nkeep);
    squeeze(buf, got);
    squeeze(want, exp);
    if (strlen(buf) != n || strcmp(got, exp) != 0) {
        printf("FAIL %s\n  got:  %s\n  want: %s\n", name, got, exp);
        failures++;
    } else {
        printf("ok   %s\n", name);
    }
    free(buf);
    free(got);
    free(exp);
}

int main(void)
{
    const char *const d_b[] = {"b", NULL};
    const char *const d_ab[] = {"a", "b", NULL};
    const char *const d_bc[] = {"b", "c", NULL};
    const char *const d_x[] = {"x", NULL};

    check("first member", "{\"b\":1,\"a\":2}", d_b, "{\"a\":2}");
    check("middle member", "{\"a\":1,\"b\":2,\"c\":3}", d_b, "{\"a\":1,\"c\":3}");
    check("last member", "{\"a\":1,\"b\":2}", d_b, "{\"a\":1}");
    check("only member", "{\"b\":{\"x\":[1,2]}}", d_b, "{}");
    check("all members", "{\"a\":1,\"b\":2}", d_ab, "{}");
    check("two at the end", "{\"a\":1,\"b\":2,\"c\":3}", d_bc, "{\"a\":1}");
    check("two adjacent middle", "{\"a\":0,\"b\":1,\"c\":2,\"d\":3}", d_bc, "{\"a\":0,\"d\":3}");
    check("nested in array of objects",
          "{\"Peers\":[{\"ID\":1,\"x\":{\"y\":\"}]\"},\"Key\":\"k1\"},{\"x\":[],\"ID\":2}]}", d_x,
          "{\"Peers\":[{\"ID\":1,\"Key\":\"k1\"},{\"ID\":2}]}");
    check("value is not a key",
          "{\"a\":\"b\",\"list\":[\"b\",{\"b\":true}],\"c\":\"b\"}", d_b,
          "{\"a\":\"b\",\"list\":[\"b\",{}],\"c\":\"b\"}");
    check("escaped quotes in strings",
          "{\"a\":\"q\\\"b\\\":1,\",\"b\":\"x\\\\\",\"c\":null}", d_b,
          "{\"a\":\"q\\\"b\\\":1,\",\"c\":null}");
    check("whitespace and literals",
          "{ \"a\" : [ 1 , 2 ] ,\n \"b\" : false ,\n \"c\" : -1.5e3 }", d_b,
          "{\"a\":[1,2],\"c\":-1.5e3}");
    check("no match", "{\"a\":{\"c\":[{\"d\":1}]}}", d_b, "{\"a\":{\"c\":[{\"d\":1}]}}");
    check("unterminated string left alone", "{\"a\":1,\"b\":\"oops", d_b, "{\"a\":1,\"b\":\"oops");
    check("MapResponse shape",
          "{\"Node\":{\"Addresses\":[\"100.1.2.3/32\"],\"Hostinfo\":{\"OS\":\"linux\",\"Services\":[{\"Proto\":\"tcp\"}]},\"Key\":\"nodekey:1\"},"
          "\"Peers\":[{\"ID\":7,\"Hostinfo\":{\"NetInfo\":{\"DERP\":9}},\"CapMap\":{\"a\":null},\"Endpoints\":[\"1.2.3.4:41641\"]}],"
          "\"UserProfiles\":[{\"ID\":1,\"LoginName\":\"x\"}],\"DNSConfig\":{\"Domains\":[]},\"PacketFilter\":[]}",
          ML_JSON_PRUNE_MAP_KEYS,
          "{\"Node\":{\"Addresses\":[\"100.1.2.3/32\"],\"Key\":\"nodekey:1\"},"
          "\"Peers\":[{\"ID\":7,\"Endpoints\":[\"1.2.3.4:41641\"]}],\"PacketFilter\":[]}");

    /* DERP region pruning */
    const char *dm =
        "{\"Node\":{\"HomeDERP\":9},\"DERPMap\":{\"Regions\":{"
        "\"1\":{\"RegionID\":1,\"Nodes\":[{\"HostName\":\"derp1.x\"}]},"
        "\"9\":{\"RegionID\":9,\"Nodes\":[{\"HostName\":\"derp9.x\",\"Name\":\"9a\"}]},"
        "\"10\":{\"RegionID\":10,\"Nodes\":[]},"
        "\"19\":{\"RegionID\":19,\"Nodes\":[{\"HostName\":\"}\"}]}"
        "},\"OmitDefaultRegions\":false},\"Peers\":[]}";
    const unsigned home9[] = {9, 9};
    const unsigned home19[] = {19};
    const unsigned home1[] = {1, 0};
    const unsigned home1_10[] = {1, 10};
    const unsigned absent[] = {42};
    check_regions("keep middle", dm, home9, 2,
                  "{\"Node\":{\"HomeDERP\":9},\"DERPMap\":{\"Regions\":{"
                  "\"9\":{\"RegionID\":9,\"Nodes\":[{\"HostName\":\"derp9.x\",\"Name\":\"9a\"}]}"
                  "},\"OmitDefaultRegions\":false},\"Peers\":[]}");
    check_regions("keep last", dm, home19, 1,
                  "{\"Node\":{\"HomeDERP\":9},\"DERPMap\":{\"Regions\":{"
                  "\"19\":{\"RegionID\":19,\"Nodes\":[{\"HostName\":\"}\"}]}"
                  "},\"OmitDefaultRegions\":false},\"Peers\":[]}");
    check_regions("keep first (0 ignored)", dm, home1, 2,
                  "{\"Node\":{\"HomeDERP\":9},\"DERPMap\":{\"Regions\":{"
                  "\"1\":{\"RegionID\":1,\"Nodes\":[{\"HostName\":\"derp1.x\"}]}"
                  "},\"OmitDefaultRegions\":false},\"Peers\":[]}");
    check_regions("keep two, not adjacent", dm, home1_10, 2,
                  "{\"Node\":{\"HomeDERP\":9},\"DERPMap\":{\"Regions\":{"
                  "\"1\":{\"RegionID\":1,\"Nodes\":[{\"HostName\":\"derp1.x\"}]},"
                  "\"10\":{\"RegionID\":10,\"Nodes\":[]}"
                  "},\"OmitDefaultRegions\":false},\"Peers\":[]}");
    check_regions("home absent: untouched", dm, absent, 1, dm);
    check_regions("no DERPMap: untouched", "{\"Peers\":[{\"ID\":1}]}", home9, 2, "{\"Peers\":[{\"ID\":1}]}");
    /* Regressions found by the fuzzer (test/fuzz). check_regions() copies into
     * a buffer of exactly strlen() bytes, so with ASan / guard pages an
     * overread past the end is caught here too. */
    check_regions("truncated after DERPMap colon", "  \r\n {\"Node\":null,\"DERPMap\":", home9, 2,
                  "  \r\n {\"Node\":null,\"DERPMap\":");
    check_regions("truncated after Regions colon", "{\"DERPMap\":{\"Regions\":   ", home9, 2,
                  "{\"DERPMap\":{\"Regions\":   ");
    check_regions("DERPMap nested in Node is ignored",
                  "{\"Node\":{\"DERPMap\":{\"Regions\":{\"1\":{}}}},\"PeersChanged\":[]}", home9, 2,
                  "{\"Node\":{\"DERPMap\":{\"Regions\":{\"1\":{}}}},\"PeersChanged\":[]}");

    printf("%s (%d failure%s)\n", failures ? "FAILED" : "PASSED", failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}

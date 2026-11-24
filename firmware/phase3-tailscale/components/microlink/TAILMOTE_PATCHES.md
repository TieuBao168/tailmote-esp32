# Local patches to MicroLink

Base: MicroLink **v2.3.0-2-g1cb8d1c** (see `VENDORED.md`). Every change is
marked `[tailmote patch]` in the source. Measurements: `docs/03-phase3-microlink-audit.md`,
sections 6 and 7 (ESP32-D0WD, no PSRAM, Tailscale hosted, 4-node tailnet).

## 1. Task stacks overridable from the application (`include/microlink_internal.h`)

`ML_TASK_{NET_IO,DERP_TX,COORD,WG_MGR}_STACK` take `CONFIG_TM_ML_*_STACK` when
the application defines it (`firmware/phase3-tailscale/main/Kconfig.projbuild`),
otherwise the upstream values. Upstream totals 42 KB; measured use was
3.4 / 3.6 / 6.9 / 2.7 KB, so the app sets 6 / 7 / 9 / 5 KB (≥ 2 KB margin each,
checked again after the change).

## 2. Prune MapResponse JSON before cJSON parses it (`src/ml_json_prune.c`, `include/ml_json_prune.h`, `src/ml_coord.c`)

**Problem.** MicroLink parses the whole MapResponse into a cJSON tree (2–3× the
text). On the no-PSRAM board the 21 KB netmap that arrives on the long-poll
right after `CONNECTED` (DERP TLS session already up) ran the heap down to
16 B, the allocation failed and the message was dropped:
`Long-poll message: JSON parse failed (21186 bytes)`. Every later full netmap
would have failed the same way.

**Change.** `prune_map()` in `ml_coord.c` runs before each of the three map
parses (initial fetch, long-poll initial netmap, long-poll messages):

- `ml_json_prune()` overwrites with spaces every member, at any depth, whose
  key MicroLink never reads (`Hostinfo`, `UserProfiles`, `DNSConfig`,
  `SSHPolicy`, `CapMap`, `Capabilities`, `ControlDialPlan`, `CollectServices`,
  `DisplayMessages`, `ClientVersion`, `TKAInfo`, `DefaultAutoUpdate`, `Debug`;
  checked against every `cJSON_GetObjectItem` in `src/`).
- `ml_json_prune_derp_regions()` keeps only the home DERP region
  (`derp_home_region` and `CONFIG_ML_DERP_REGION`) in `DERPMap.Regions`.
  MicroLink only connects to and STUNs the home region. If the home region is
  not in the map (custom control server with its own DERP map), nothing is
  pruned so the upstream "first usable region" fallback still works.

The text keeps its length and stays valid JSON (same buffers, same offsets).
Both functions scan with constant memory (they run on the 9 KB `ml_coord`
stack). Host test: `firmware/phase3-tailscale/test/test_json_prune.c` (21
cases, run by `tm test` when gcc is available).

**Result.** 17.7 KB of the 21.2 KB netmap pruned (2.2 KB keys + 15.5 KB DERP
regions); no allocation failure; heap low-water mark 36 KB instead of 16 B;
steady free heap 69 KB, largest block 26.6 KB.

**Limit.** A peer still costs its full entry (minus `Hostinfo`/`CapMap`). The
next step for large tailnets is a streaming parser (docs/03 section 1,
condition 2).

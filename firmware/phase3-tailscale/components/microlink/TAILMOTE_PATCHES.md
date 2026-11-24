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

Both pruners run only on **well-formed JSON** (`json_valid()`: strict
RFC 8259 syntax, constant memory, nesting ≤ 48); anything else is left
untouched for `cJSON_Parse()` to reject as before. `prune_map()` runs the
region pass before the key pass. With these two rules pruning is idempotent
(see section 3).

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

## 3. Fuzzing (step 3.4)

`firmware/phase3-tailscale/test/fuzz/`: `fuzz_json_prune.c` is a libFuzzer
target (`LLVMFuzzerTestOneInput`); `driver.c` runs it without libFuzzer/ASan
(MinGW gcc on Windows) with structure-aware generation + mutation, and puts
every working buffer against a guard page so a one-byte overread faults.
Oracles: only bytes → spaces, idempotent, valid stays valid, and for valid
RFC JSON the pruned tree equals the input tree minus the dropped members
(compared as serialised cJSON, so duplicate keys count). `tm fuzz --seconds N`;
`tm test` runs 3 s of it.

Bugs it found in this patch (all fixed, cases kept in `test/test_json_prune.c`
or covered by the validator):

| # | Input | Bug | Fix |
|---|---|---|---|
| 1 | `{"Node":null,"DERPMap":` (truncated) | `find_member()` returned the end index; caller read `s[n]` (1-byte overread) | return 0 when no value follows |
| 2 | malformed region + key pass | key pass stopped at an error, region pass then blanked the error, a 2nd pass pruned more | regions first, then keys |
| 3 | `{"DNSConfig":{..} "Debug":..}` (missing comma) | a pruned member turned the next value into a key | error if a pruned member is not followed by `,` or `}` |
| 4 | `"k":¢"}` | literal skipping swallowed a `"`, shifting string boundaries | literals only `true`/`false`/`null`/number chars |
| 5 | `}},,"DNSConfig"` (double comma) | tolerant scanner accepted it; pruning made it acceptable to the region pass | prune only when `json_valid()` passes |

After the last fix: ~9.8 million inputs (5 × 60 s + 600 s), 0 failures. The
real Tailscale netmap still passes `json_valid()` and is pruned as before
(checked on the board).

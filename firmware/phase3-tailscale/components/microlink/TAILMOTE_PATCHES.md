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
stack). Host test: `firmware/phase3-tailscale/test/test_json_prune.c` (23
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

## 4. DERP write stall budget: 5 s, time based (`src/ml_derp.c`)

`derp_tls_write_all()` gave up after 50 × 10 ms (500 ms) of `WANT_WRITE`,
counting from the last partial write, and the caller then tore down and rebuilt
the DERP TLS session. With the board as an exit node for a relayed phone
(symmetric NAT + 4G CGNAT, so every packet goes through DERP), the small lwIP
send buffer stayed full for longer than that: in 5 minutes of a speed test the
session was rebuilt 14 times, 291 packets were dropped while it was down, and
each rebuild (seconds of CPU, ~30 KB of heap) pushed the heap low enough that
mbedTLS failed to allocate its 4.4 KB output record (`-0x7f00`), which broke
the session again.

The budget is now 5 s without progress (`DERP_WRITE_STALL_MS`), matching the
5 s payload read timeout; any progress restarts it. A stream that is really
dead still fails, after 5 s instead of 0.5 s. Re-test: stall timeouts fell from
8 to 2 in a comparable run.

## 5. DERP TX queue depth 8, overridable (`include/microlink_internal.h`)

`ML_DERP_TX_QUEUE_DEPTH` was 16; each queued packet is a heap copy of up to
~1.3 KB, so a full queue held ~21 KB while the TLS stream was backed up. With
the 5 s stall budget above, the remaining DERP rebuilds under load were all
mbedTLS failing to allocate its 4.4 KB output record (`-0x7f00`, 27 times in
15 minutes). Now 8 (wrapped in `#ifndef` so a build can override it), together
with fewer Wi-Fi RX buffers for exit-node builds (`tm config`: 10). A larger
lwIP TCP send buffer (11488) was tried first and made the heap pressure worse.

## 6. One TLS record per DERP frame (`src/ml_derp.c`)

`derp_write_frame()` wrote the 5-byte frame header and the payload with two
`mbedtls_ssl_write()` calls, so every packet sent through DERP became two TLS
records: two AEAD operations, two record headers and, with
`CONFIG_MBEDTLS_DYNAMIC_BUFFER`, two output-buffer allocations. Header and
payload now go out in one buffer and one write (`derp_put_header()`;
`derp_send_packet()` builds header + key + payload in a single allocation,
frames of up to 64 bytes use the stack).

## 7. Shed WireGuard data before the DERP queue is full; DERP counters

`ml_derp_queue_send()` used to accept data until the queue was full and then
evict the oldest entries. By then the queue and the backed-up TLS stream held
the heap that the TLS output record needs. WireGuard transport data (type 4,
which includes all exit-node traffic) is now dropped once
`ML_DERP_TX_QUEUE_DEPTH - ML_DERP_TX_RESERVED` (8 - 2) packets are queued.
Handshakes, cookie replies and DISCO can still use the last slots, and TCP on
the peer backs off.

Counters on `ml_derp_conn_t`, exposed with `microlink_get_derp_stats()`:
`tx_shed` (dropped early, above), `tx_down` (dropped by `wg_derp_output_cb`
while DERP was disconnected) and `reconnects`. The application prints them as
`METRIC {"ev":"derp",...}`, and `tools/bench/soak.py` grades them as case S8.

## 8. DERP TLS: never return part of a record header; ENOMEM is back-pressure (`src/ml_derp.c`)

The DERP session failed under load with `DERP header read returned -29184
(0x7200)`, which is `MBEDTLS_ERR_SSL_INVALID_RECORD`. The cause is in ESP-IDF's
dynamic RX buffer (`CONFIG_MBEDTLS_DYNAMIC_BUFFER`,
`esp_mbedtls_add_rx_buffer()` in IDF v5.3.6). It fetches each 5-byte TLS record
header into a local stack array (`msg_head[5]`), while
`mbedtls_ssl_fetch_input()` keeps the number of header bytes already read
(`in_left`) in the SSL context. On our non-blocking socket, a header split
across TCP segments returns `WANT_READ` part-way. The bytes already read are
then lost with the stack frame, the next call completes the header with stack
garbage, and parsing fails. Split headers happen more often at high packet
rates, which is why the error clustered in speed tests.

`ml_derp_bio_recv()` now stages requests of at most 13 bytes
(`DERP_BIO_WHOLE_MAX`, which covers the 5-byte TLS header) in
`ml_derp_conn_t.hdr_stage`. That buffer survives across calls and is reset for
every new socket. The request is handed to mbedTLS only once it is complete,
so `in_left` never holds a partial header. Longer requests (record bodies) go
into the heap RX buffer, which survives partial reads, and are read straight
from the socket. A first version used `recv(MSG_PEEK)` instead. lwIP peeks
only inside the current pbuf, never across the segment boundary that splits
the header, so RX stalled and throughput halved. Do not go back to it.

`ml_derp_bio_send()` also maps `ENOMEM` / `ENOBUFS` (lwIP `ERR_MEM`: pbufs or
TCP queue slots briefly exhausted) to `WANT_WRITE`, like `EAGAIN`, within the
5 s stall budget of patch 4. These used to come back as
`MBEDTLS_ERR_NET_SEND_FAILED` (-0x0050) and rebuild the session (4 times in
4 minutes of load).

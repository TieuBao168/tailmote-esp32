# Local patches to esp_wireguard

Base: [trombik/esp_wireguard](https://github.com/trombik/esp_wireguard) **0.9.0**
(commit `9217c5be0836e908005301ad2c2d42009e560c0e`), BSD-3-Clause, see `LICENSE`.
Vendored unmodified in commit "Vendor esp_wireguard 0.9.0 unmodified into
components/". `examples/` and `component.mk` were dropped. Every change below
is marked `[tailmote patch]` in the source.

## 1. Peer liveness: `esp_wireguardif_peer_rx_age_ms()`

**Problem.** `esp_wireguardif_peer_is_up()` returns OK whenever a session key
exists (`curr_keypair.valid || prev_keypair.valid`). After the peer goes away
the key stays valid, so the application cannot tell a dead peer from an idle
one. The library also does not implement WireGuard's "no packet received
within KEEPALIVE + REKEY_TIMEOUT after sending → new handshake" rule. Measured
on hardware (docs/02, section 6.2): after a 30 s peer restart the tunnel only
came back once the 120 s `REKEY_AFTER_TIME` rekey fired, 71 s after the peer
was back.

**Change.**
- `src/wireguardif.c` / `src/wireguardif.h`: `wireguardif_peer_rx_age()`
  returns the milliseconds since the later of `peer->last_rx` (any
  authenticated data or keepalive packet) and `curr_keypair.keypair_millis`
  (a completed handshake counts as contact). Returns `ERR_CONN` without a
  valid session.
- `src/esp_wireguard.c` / `include/esp_wireguard.h`: public wrapper
  `esp_wireguardif_peer_rx_age_ms(ctx, &age_ms)`.

Reads the 32-bit fields from the caller's task without locking, like
`esp_wireguardif_peer_is_up()` already does. 32-bit loads are atomic on
Xtensa and a stale value only shifts detection by one poll.

**Use** (firmware `main/wg_tunnel.c`): with persistent keepalive on both ends
(the peer config from `tm config` sets `PersistentKeepalive`), "stale" means
nothing for `KEEPALIVE + 15` s, and the tunnel is torn down and re-handshaked
after `CONFIG_POC_WG_PEER_DOWN_RESTART_S` (45 s). Result: recovery after a
30 s peer restart went from 71 s to 15 s.

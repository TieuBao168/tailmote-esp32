# esp32-tailscale

**English** · [Tiếng Việt](README.vi.md)

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![ESP-IDF v5.3.6](https://img.shields.io/badge/ESP--IDF-v5.3.6-red.svg)
![Target: ESP32](https://img.shields.io/badge/target-ESP32%20(no%20PSRAM)-green.svg)
![Platforms: Linux | macOS | Windows](https://img.shields.io/badge/host-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)

Run an **ESP32 as a native Tailscale node**, with no Linux gateway and no external
PSRAM. The board joins a tailnet on its own: it registers with the control server,
receives the netmap, reaches peers directly through NAT traversal (DISCO/STUN), and
falls back to DERP relays when it has to.

Reference hardware: **ESP32 DOIT DevKit V1** (ESP32-D0WD rev 1.0, 520 KB SRAM,
4 MB flash, **no PSRAM**). ESP8266 is out of scope because it does not have enough
RAM (see the Phase 1 report).

> [!IMPORTANT]
> This is a research project, not a production-hardened product. Flash and NVS
> encryption are **not** enabled, so the device keys and the Tailscale auth key are
> stored unencrypted on the chip. Read [Security notes](#security-notes) before you
> deploy a board anywhere you do not control.

---

## Contents

- [Highlights](#highlights)
- [Project status](#project-status)
- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Command reference](#command-reference)
- [Phase 2: WireGuard PoC and benchmarks](#phase-2-wireguard-poc-and-benchmarks)
- [Testing](#testing)
- [Security notes](#security-notes)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Highlights

Measured on the reference board against the hosted Tailscale control server, with a
4-node tailnet ([docs/03 §9–10](docs/03-phase3-microlink-audit.md#9-bước-35-test-cuối-2026-11-24)):

| Metric | Result |
|---|---|
| Time from boot to `CONNECTED` | **9.8 s** (DERP home region Singapore) |
| UDP throughput, direct path | up to **~4 Mbit/s**; 0 % loss at ≤ 2 Mbit/s |
| RTT, direct path (64 B) | p50 22 ms, p95 100 ms |
| Recovery after a Wi-Fi outage | **< 7 s** after the link returns |
| 30-minute soak | 0 reboots, 0 allocation failures, 0 reconnects |
| Free heap during soak | ~68 KB steady (40.5 KB minimum), no leak |
| CPU load | 2.9 % on core 0, 1.2 % on core 1 |
| Firmware image | ~966 KB (`--prod`), 51 % of the app partition free |

## Project status

| Phase | Scope | Status |
|---|---|---|
| 1. Feasibility | [docs/01-feasibility-report.md](docs/01-feasibility-report.md) | ✅ Done: **conditional GO** for the DOIT DevKit V1 |
| 2. WireGuard PoC | [docs/02-phase2-wireguard-poc.md](docs/02-phase2-wireguard-poc.md), [`firmware/phase2-wg-poc`](firmware/phase2-wg-poc) | ✅ Done (2026-11-24): a plain WireGuard tunnel runs on real hardware |
| 3. Tailscale integration | [docs/03-phase3-microlink-audit.md](docs/03-phase3-microlink-audit.md), [`firmware/phase3-tailscale`](firmware/phase3-tailscale) | ✅ Done (2026-11-24): the board is a Tailscale node, with a production build profile |
| 4. Optimization | Streaming JSON parser, RAM tuning, 24 h soak | ⏳ Not started |

Not yet verified: the DERP path from a host outside the LAN, blocking ACL policies,
a 24-hour soak, automated fuzzing of the Noise/HTTP/2 layer, and flash/NVS encryption.

## How it works

```
                 ┌───────────────── ESP32 (no PSRAM) ─────────────────┐
  Tailscale      │  main (tailmote_phase3)  ── METRIC telemetry ──► UART │
  control  ◄──── │  MicroLink v2.3.0 (vendored + 8 patches)            │
  server  Noise  │    ├─ ts2021 / Noise IK + HTTP/2 control client     │
  (ts2021)       │    ├─ MapResponse pruner (fits netmap in SRAM)      │
                 │    ├─ DISCO / STUN NAT traversal ───────────────────┼──► peers (direct UDP)
  DERP relay ◄── │    ├─ DERP relay client ───────────────────────────┼──► peers (relayed)
                 │    └─ wireguard-lwip  →  lwIP  →  esp_wifi          │
                 └─────────────────────────────────────────────────────┘
```

The Phase 3 firmware builds on [MicroLink](https://github.com/CamM2325/microlink), which
is vendored in [`components/microlink`](firmware/phase3-tailscale/components/microlink)
together with 8 patches. Those patches make it fit in internal RAM, fix lost netmaps and
fix 5 bugs that fuzzing found in the JSON pruner. Each patch is documented in
[`TAILMOTE_PATCHES.md`](firmware/phase3-tailscale/components/microlink/TAILMOTE_PATCHES.md).

## Repository layout

```
Makefile                      Entry point on Linux/macOS (`make help`)
tm.cmd                        Entry point on Windows (`.\tm help`); same command names
scripts/tm.py                 All setup/config/build/flash/peer/bench logic (Python 3.8+, stdlib only)
scripts/install-deps.sh       System packages for Linux (`make deps`)
config/project.env            Non-secret settings: ESP-IDF version, paths, firmware project
config/secrets.env.example    Secrets form: Wi-Fi, WireGuard keys, Tailscale auth key, serial port
firmware/
  phase2-wg-poc/              ESP-IDF project: WireGuard tunnel + RAM/CPU/timing metrics + bench services
    components/esp_wireguard/ esp_wireguard 0.9.0, vendored and patched
  phase3-tailscale/           ESP-IDF project: Tailscale node (default build target)
    components/microlink/     MicroLink v2.3.0, vendored and patched
    sdkconfig.prod.defaults   Production overlay (`tm build --prod`)
    test/                     Host unit tests and fuzz harness for the JSON pruner
tools/bench/                  Benchmark, recovery and loss-probe clients; serial capture; metric parser
tests/                        Tooling unit tests (`make test`)
docs/                         Per-phase reports, measurements and design decisions
secrets/                      (git-ignored) filled-in form, generated keys, peer config
results/                      (git-ignored) serial logs and measurement output
```

## Requirements

Linux, macOS and **Windows 10/11** are all supported as build hosts. Windows does not
need WSL or a Linux machine.

| | Linux (Ubuntu/Debian) | Windows 10/11 |
|---|---|---|
| Command prefix | `make <cmd>` | `.\tm <cmd>` (PowerShell or cmd, in the repo root) |
| Prerequisites | `make`, Python 3.8+ | Git, Python 3.8+ (`winget install Git.Git Python.Python.3.12`) |
| System packages | `make deps` (apt, needs sudo) | Not needed: `.\tm idf` downloads cmake, ninja and the toolchain |
| ESP-IDF **v5.3.6** | `make idf` → `~/esp/esp-idf-v5.3.6`, tools in `~/.espressif` | `.\tm idf` → `C:\Espressif\frameworks\esp-idf-v5.3.6`, tools in `C:\Espressif` |
| USB-serial driver | Built into the kernel; your user must be in the `dialout` group | CH340 (WCH CH341SER) or CP210x (Silicon Labs) |
| WireGuard peer (Phase 2 only) | `wireguard-tools`, sudo | [WireGuard for Windows](https://www.wireguard.com/install/), elevated PowerShell |
| Disk | ~4 GB free | ~4 GB free |

If you only have Docker, use `make docker-build` (or `.\tm docker-build`). It builds
with the `espressif/idf:v5.3.6` image.

`make doctor` checks every row above and reports what is missing. It does not change
anything.

## Quick start

The steps below build and run the **Phase 3 Tailscale node**. On Windows, replace
`make <cmd>` with `.\tm <cmd>`.

### 1. Set up the toolchain (once)

```bash
make doctor
```

```bash
make deps
```

```bash
make idf
```

On Windows, skip `deps`.

### 2. Fill in the secrets form

```bash
make secrets
```

This creates `secrets/secrets.env`, which is git-ignored. Fill in at least these fields:

| Key | Purpose |
|---|---|
| `WIFI_SSID`, `WIFI_PASSWORD` | 2.4 GHz WPA2 network |
| `TS_AUTH_KEY` | Create one at the [Tailscale admin console](https://login.tailscale.com/admin/settings/keys) (*Reusable* + *Pre-approved* recommended). If you leave it empty, the board prints a login URL on the serial console instead. |
| `TS_DEVICE_NAME` | Hostname on the tailnet (default `tailmote-esp32`) |
| `TS_DERP_REGION` | Home DERP region ID. Pick the nearest one using `tailscale netcheck`, e.g. `3` = Singapore, `20` = Hong Kong, `7` = Tokyo. If you leave it empty, MicroLink uses region `9` (Dallas). |
| `SERIAL_PORT` | Recommended when more than one USB-serial adapter is plugged in, e.g. `/dev/serial/by-id/usb-1a86_…` or `COM7`. If you leave it empty, the first adapter found is used. |

Then generate the build configuration:

```bash
make config
```

`config` validates the form and writes `sdkconfig.secrets` for both firmware projects,
plus the WireGuard keys and peer config used by Phase 2. You can run it again safely:
existing keys are kept.

### 3. Build

```bash
make build
```

Production profile (test hooks off, `WARN` log level, `METRIC` lines kept):

```bash
python3 scripts/tm.py build --prod
```

On Windows: `.\tm build --prod`. `make build` does not pass `--prod` through.

`build` regenerates `sdkconfig` automatically whenever `sdkconfig.defaults`,
`sdkconfig.secrets` or the profile changes. When that happens, changes you made with
`menuconfig` are discarded.

### 4. Flash and monitor

```bash
make flash-monitor
```

Watch the console until MicroLink reports `CONNECTED`. The board then appears in the
Tailscale admin console, and you can reach it from any tailnet peer at its `100.x.y.z`
address. Press `Ctrl+]` to leave the monitor.

## Command reference

`make help` (Linux/macOS) and `.\tm help` (Windows) print this list.

| Command | Description |
|---|---|
| `doctor` | Check the environment without changing anything |
| `deps` | Install system packages (Linux: apt + sudo; Windows: prints instructions) |
| `idf` | Clone and install ESP-IDF and the esp32 toolchain |
| `secrets` | Create `secrets/secrets.env` from the form (never overwrites) |
| `config` | Validate the form; generate keys, peer config and `sdkconfig.secrets` |
| `build` | Build the firmware (`--prod` for the production profile) |
| `size` | Static flash/RAM report → `results/` |
| `flash` / `monitor` / `flash-monitor` | Flash, open the serial monitor, or both |
| `menuconfig` / `clean` / `fullclean` | Standard `idf.py` actions (`fullclean` also removes `sdkconfig`) |
| `docker-build` | Build inside the `espressif/idf` image |
| `peer-up` / `peer-down` / `peer-status` | Manage the Phase 2 WireGuard peer on this host |
| `log` | Reset the board and capture serial output → `results/serial.log` |
| `bench` | Run the Phase 2 benchmark suite (`--host <ip>` for a LAN baseline, `--label …`) |
| `metrics` | Summarize `results/serial.log` → `results/periodic.csv` |
| `fuzz` | Fuzz MicroLink's JSON pruner on the host (`--seconds N`, `--seed S`) |
| `test` | Tooling and host-side unit tests (no board or ESP-IDF needed) |

`fuzz` has no `make` target. Run it with `python3 scripts/tm.py fuzz` (or `.\tm fuzz`).

The firmware project defaults to Phase 3. To build the Phase 2 PoC, override
`FW_PROJECT`:

```bash
FW_PROJECT=firmware/phase2-wg-poc make build
```

On Windows: `$env:FW_PROJECT='firmware/phase2-wg-poc'; .\tm build`.

## Phase 2: WireGuard PoC and benchmarks

Phase 2 sets up a plain WireGuard tunnel between the ESP32 and a peer on the same LAN.
It was used to measure the RAM, CPU and timing cost of WireGuard before any Tailscale
logic was added. The host running the peer must be on the same LAN as the board. On
Windows it needs an elevated PowerShell: `peer-up` installs the `wg-esp` tunnel and
opens UDP `WG_PORT` in Windows Firewall, and `peer-down` removes both.

```bash
make peer-up
```

```bash
FW_PROJECT=firmware/phase2-wg-poc make flash-monitor
```

The expected log sequence is `METRIC {"ev":"boot"…}`, then `wifi_got_ip`,
`sntp_synced` and finally `wg_up`. `make peer-status` should then show a
`latest handshake`.

```bash
make log
```

```bash
make bench
```

```bash
make bench HOST=<ESP32 LAN IP>
```

```bash
make metrics
```

```bash
make peer-down
```

Run `make log` in a separate terminal while you measure. `make bench HOST=…` measures
a baseline without the tunnel. The measurement matrix (M1–M13), the recovery scenarios
and the results are in [docs/02](docs/02-phase2-wireguard-poc.md).

## Testing

| Layer | How |
|---|---|
| Tooling (`tm.py`: env parsing, validation, X25519 key generation against RFC 7748 vectors) | `make test` |
| MicroLink JSON pruner, host build | `make test` (via `tests/test_json_prune_c.py`) |
| JSON pruner fuzzing | `python3 scripts/tm.py fuzz --seconds 60` |
| On-device | `METRIC` telemetry on the serial console, plus `tools/bench/ts_recovery.py` and `ts_lossprobe.py` over the tailnet |

## Security notes

- **No flash or NVS encryption.** Machine, node and disco keys, and the auth key, are
  stored in plaintext in NVS and in the firmware image. The production profile
  deliberately does **not** enable encryption, because doing so burns eFuses on first
  boot, which cannot be undone. Also, chip rev 1.0 only supports Secure Boot V1.
  Enable encryption on purpose on product hardware by following the ESP-IDF flash
  encryption guide.
- **Treat a flashed board as a credential.** Use a tagged, pre-approved auth key with
  the minimum ACL scope, and revoke the node in the admin console if a board is lost.
- **Pre-authentication parsing was audited by hand** ([docs/03 §10.2](docs/03-phase3-microlink-audit.md#102-rà-soát-tay-phần-nhận-trước-khi-xác-thực)).
  No memory-safety bugs were found. An on-path attacker can force a reconnect by
  desynchronizing the receive nonce, but cannot read or modify data.
- **Test hooks** (`CONFIG_TM_TEST_HOOKS`, e.g. `WIFI_DOWN <s>`) are enabled in the
  default build and disabled by `--prod`. Do not deploy a non-`--prod` image.
- Never commit anything from `secrets/` or any `sdkconfig.secrets` file. Both are
  git-ignored.

## Known limitations

- About **8 peers** at most, until a streaming JSON parser replaces the
  netmap pruner (Phase 4).
- Board-side services are UDP only. TCP throughput has not been measured.
- The board sends an endpoint update about every 23 s even when its endpoint has not
  changed. This is harmless but wastes traffic and power (Phase 4).
- The reference board is ESP32-D0WD rev 1.0, which is suitable for research only.
  Use rev ≥ 3.0 for Secure Boot V2.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make doctor` reports something missing | Run the command it suggests (`make deps`, `make idf`, `make config`) |
| Build warns *"no sdkconfig.secrets"* | Run `make config`. Without it the firmware has no Wi-Fi or Tailscale credentials |
| Flashing hits the wrong device | Set `SERIAL_PORT` in `secrets/secrets.env`. On Linux, prefer a `/dev/serial/by-id/…` path |
| `Permission denied` on `/dev/ttyUSB*` | Add your user to `dialout` (`sudo usermod -aG dialout $USER`), then log in again |
| Windows: `CERTIFICATE_VERIFY_FAILED` during `.\tm idf` | `$env:IDF_GITHUB_ASSETS='dl.espressif.com/github_assets'; .\tm idf` |
| Windows: ESP-IDF fails on paths with spaces | Move `IDF_PATH` / `IDF_TOOLS_PATH` in `config/project.env` to a path without spaces |
| Slow `CONNECTED` or high relay latency | Set `TS_DERP_REGION` to the region closest to you |
| Board prints a login URL | `TS_AUTH_KEY` is empty or expired. Approve the URL, or set a new key and run `make config build flash` |

## License

This project is licensed under [Apache-2.0](LICENSE); see also [NOTICE](NOTICE). Every
source file carries an `SPDX-License-Identifier` header. Third-party components
(ESP-IDF, esp_wireguard, wireguard-lwip, MicroLink, …) keep their original licenses,
listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). If you distribute a
firmware binary, you must include their notices.

This is an independent project. It is not affiliated with or endorsed by Tailscale Inc.
or Espressif Systems. "Tailscale" and "WireGuard" are trademarks of their respective
owners.

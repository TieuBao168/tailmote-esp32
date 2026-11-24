# Third-Party Notices

Mã nguồn của dự án này dùng giấy phép Apache-2.0 ([LICENSE](LICENSE)). Các thành phần bên thứ ba dưới đây giữ nguyên giấy phép gốc. Repo **không chứa** mã nguồn của chúng; chúng được tải lúc cài đặt hoặc build.

## Dùng khi build firmware

| Thành phần | Phiên bản | Giấy phép | Cách đưa vào | Nguồn |
|---|---|---|---|---|
| ESP-IDF (FreeRTOS, lwIP, mbedTLS, esp_wifi …) | v5.3.6 | Apache-2.0 (từng component có license riêng: FreeRTOS MIT, lwIP BSD-3-Clause, mbedTLS Apache-2.0; Wi‑Fi/PHY là binary blob của Espressif) | `make idf` → `~/esp` | https://github.com/espressif/esp-idf |
| trombik/esp_wireguard | 0.9.0 (commit 9217c5be), **có vá** | BSD-3-Clause; `src/nacl/crypto_scalarmult/curve25519/ref/smult.c` là public domain | Nhúng trong repo: `firmware/phase2-wg-poc/components/esp_wireguard/` (giữ `LICENSE` gốc; thay đổi ghi trong `TAILMOTE_PATCHES.md`) | https://github.com/trombik/esp_wireguard |
| wireguard-lwip (smartalock) | (nhúng trong esp_wireguard và MicroLink) | BSD-3-Clause | như trên | https://github.com/smartalock/wireguard-lwip |
| CamM2325/microlink | v2.3.0-2-g1cb8d1c, **có vá** | MIT (`components/wireguard_lwip` bên trong: BSD-3-Clause) | Nhúng trong repo: `firmware/phase3-tailscale/components/microlink/` (`LICENSE`, `VENDORED.md`, `TAILMOTE_PATCHES.md`) | https://github.com/CamM2325/microlink |

Firmware build ra (`.bin`) có chứa các thành phần trên. Khi phân phối firmware, phải kèm thông báo bản quyền và giấy phép của chúng (BSD-3-Clause yêu cầu giữ thông báo trong tài liệu đi kèm bản phân phối nhị phân).

## Dùng khi chạy / đo (không đi vào firmware)

| Thành phần | Giấy phép | Ghi chú |
|---|---|---|
| wireguard-tools (`wg`, `wg-quick`) | GPL-2.0 | Cài qua apt, chỉ gọi như chương trình ngoài |
| WireGuard for Windows (`wireguard.exe`, `wg.exe`) | MIT | Cài qua winget, chỉ gọi như chương trình ngoài (`tm peer-up/down/status`) |
| pyserial | BSD-3-Clause | Có trong Python env của ESP-IDF |

## Tài liệu tham chiếu (không chép mã)

| Dự án | Giấy phép | Dùng để |
|---|---|---|
| tailscale/tailscale | BSD-3-Clause | Tham chiếu giao thức ts2021, DERP, DISCO, `tailcfg` |
| tailscale/tailscale-rs | BSD-3-Clause | Tham chiếu kiến trúc |
| juanfont/headscale | BSD-3-Clause | Control server cho môi trường test |
| MicroLink (CamM2325/microlink) | MIT | Ứng viên fork cho Phase 3 |

**Khi chép hoặc chuyển ngữ mã** từ bất kỳ dự án nào ở trên vào repo này: thêm mục vào bảng "Dùng khi build firmware", giữ nguyên header bản quyền gốc trong file, và đặt bản sao giấy phép gốc tại `third_party/<tên>/LICENSE`.

# esp32-tailscale

Nghiên cứu đưa ESP32 (nền tảng: **ESP32 DOIT DevKit V1**) vào mạng Tailscale (Tailnet) như một node, không cần Linux gateway. ESP8266 đã bị loại khỏi phạm vi (không đủ RAM, xem báo cáo Phase 1).

## Trạng thái

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 1. Feasibility | [docs/01-feasibility-report.md](docs/01-feasibility-report.md) | Xong (v0.2): **GO có điều kiện** cho DOIT DevKit V1 |
| 2. WireGuard PoC | [docs/02-phase2-wireguard-poc.md](docs/02-phase2-wireguard-poc.md), [firmware/phase2-wg-poc](firmware/phase2-wg-poc) | Code, kế hoạch đo, script setup/build xong. **Firmware chưa build, chưa chạy** (chưa có module) |
| 3. Tailscale Integration | Fork/audit MicroLink, ts2021, DERP, DISCO, PacketFilter | Chưa bắt đầu |
| 4. Optimization | Streaming JSON, tối ưu RAM, soak test | Chưa bắt đầu |

## Cấu trúc

```
Makefile                       Điểm vào cho mọi bước: `make help`
config/secrets.env.example     FORM bí mật (Wi-Fi, khóa, IP tunnel, cổng serial)
config/project.env             Thiết lập không bí mật (phiên bản ESP-IDF, đường dẫn)
scripts/                       doctor, install-deps, install-idf, new-secrets, gen-config, fw, docker-build, peer
firmware/phase2-wg-poc/        ESP-IDF project: WireGuard tunnel + đo RAM/CPU/timing + dịch vụ benchmark
tools/bench/                   wg_bench.py (client đo), serial_capture.py, parse_metrics.py
docs/                          Báo cáo và hướng dẫn từng phase
secrets/                       (git-ignored) secrets.env đã điền, khóa WireGuard, cấu hình wg Linux
results/                       (git-ignored) log và kết quả đo
```

## Môi trường cần có

| Thành phần | Yêu cầu | Cài bằng |
|---|---|---|
| OS | Ubuntu/Debian x86_64 (script dùng `apt`) | — |
| Gói hệ thống | git, wget, python3 + **python3-venv**, flex, bison, gperf, cmake, ninja-build, ccache, libffi-dev, libssl-dev, dfu-util, libusb-1.0-0, wireguard-tools | `make deps` (sudo) |
| ESP-IDF | **v5.3.6** tại `~/esp/esp-idf-v5.3.6` + toolchain xtensa-esp32 trong `~/.espressif` | `make idf` |
| Đĩa / mạng | ~4 GB trống; Internet cho lần cài và lần build đầu (tải `trombik/esp_wireguard`) | — |
| Chỉ khi chạy | Board DOIT DevKit V1, user thuộc nhóm `dialout`, sudo cho `wg-quick` | `make deps` thêm nhóm dialout |

Thay thế: chỉ có Docker → `make docker-build` (image `espressif/idf:v5.3.6`), không cần `make deps idf`.

`make doctor` kiểm tra toàn bộ bảng trên và chỉ ra mục còn thiếu, không thay đổi gì.

## Setup & Build

Các bước dưới đây **chưa được chạy trọn vẹn** (chưa build firmware). Phần form/sinh cấu hình/doctor đã chạy thử.

### 1. Môi trường (một lần)

```bash
make doctor
```

```bash
make deps
```

```bash
make idf
```

### 2. Điền form bí mật

```bash
make secrets
```

Mở `secrets/secrets.env` và điền. Bắt buộc: `WIFI_SSID`, `WIFI_PASSWORD`. Các mục khác có mặc định; để trống khóa thì script tự sinh.

| Mục | Nội dung |
|---|---|
| [1] Wi‑Fi | SSID, mật khẩu (2.4 GHz, WPA2, 8–63 ký tự hoặc trống) |
| [2] Peer Linux | `WG_ENDPOINT` (trống = IP LAN tự động), `WG_PORT` |
| [3] Tunnel | IP Linux / ESP32, netmask, prefix |
| [4] Khóa | Khóa riêng ESP32 / Linux (trống = tự sinh), preshared key (`auto` = tự sinh) |
| [5] Tunnel | Keepalive, Wi‑Fi power save (`none` / `min_modem`) |
| [6] Nạp | Cổng serial, baud |

```bash
make config
```

`make config` kiểm tra form (báo lỗi từng mục), rồi sinh `secrets/*.key|pub`, `secrets/wg-esp.conf` (peer Linux) và `firmware/phase2-wg-poc/sdkconfig.secrets`. Chạy lại an toàn: khóa đã sinh được giữ nguyên.

### 3. Build

```bash
make build
```

```bash
make size
```

`make build` tự tạo lại `sdkconfig` khi `sdkconfig.defaults` hoặc `sdkconfig.secrets` thay đổi (thay đổi riêng qua `make menuconfig` sẽ mất khi đó).

## Run & Đo (khi có board)

```bash
make peer-up
```

```bash
make flash-monitor
```

Kỳ vọng trên log: `METRIC {"ev":"boot"...}` → `wifi_got_ip` → `sntp_synced` → `wg_up`. Kiểm tra `make peer-status` có `latest handshake`.

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

`make log` ghi `results/serial.log` (chạy ở terminal riêng trong lúc đo). `make bench` đo qua tunnel; với `HOST=<IP LAN>` đo baseline không tunnel. Ma trận đo M1–M13, kịch bản phục hồi và bảng kết quả: [docs/02-phase2-wireguard-poc.md](docs/02-phase2-wireguard-poc.md).

## License

[Apache-2.0](LICENSE) — xem thêm [NOTICE](NOTICE). Các thành phần bên thứ ba (ESP-IDF, esp_wireguard, wireguard-lwip, …) giữ giấy phép gốc, liệt kê trong [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Mỗi file mã nguồn có header `SPDX-License-Identifier: Apache-2.0`.

Dự án độc lập, không liên kết với Tailscale Inc. hay Espressif Systems.

## Điểm cần xác nhận ở lần build/chạy đầu tiên

1. **`esp_wireguard` 0.9.0 với ESP-IDF v5.3**: README thư viện chỉ ghi IDF ≤ 4.4 + master. Nếu lỗi build, thử fork `esphome/wireguard` hoặc vá theo log lỗi.
2. **Tên Kconfig** trong `sdkconfig.defaults`: tên sai bị IDF bỏ qua kèm cảnh báo — kiểm tra bằng `grep` trong `sdkconfig` sau khi build.
3. **Chip revision** từ sự kiện `boot` (`rev` = major×100 + minor). Rev < 300 không có Secure Boot V2.
4. **Stack task**: giá trị hiện tại là ước lượng; tăng nếu `task_stack_free_min_bytes` < 512 B.
5. **Thread safety** khi reconnect: `esp_wireguard_connect/disconnect` gọi từ task `wg_sup` như example của thư viện.
6. **SNTP** (`pool.ntp.org`, UDP 123) phải thành công trước handshake.

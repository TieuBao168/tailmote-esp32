# esp32-tailscale

Nghiên cứu đưa ESP32 (nền tảng: **ESP32 DOIT DevKit V1**) vào mạng Tailscale (Tailnet) như một node, không cần Linux gateway. ESP8266 đã bị loại khỏi phạm vi (không đủ RAM, xem báo cáo Phase 1).

## Trạng thái

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 1. Feasibility | [docs/01-feasibility-report.md](docs/01-feasibility-report.md) | Xong (v0.2): **GO có điều kiện** cho DOIT DevKit V1 |
| 2. WireGuard PoC | [docs/02-phase2-wireguard-poc.md](docs/02-phase2-wireguard-poc.md), [firmware/phase2-wg-poc](firmware/phase2-wg-poc) | **PoC xong** (2026-11-24): tunnel chạy trên board thật, số đo ở [mục 6](docs/02-phase2-wireguard-poc.md#6-kết-quả-đo--lần-chạy-1-2026-11-24). Soak / throughput / recovery mất Wi‑Fi gộp vào test cuối Phase 3 |
| 3. Tailscale Integration | [docs/03-phase3-microlink-audit.md](docs/03-phase3-microlink-audit.md) | **Board là node Tailscale** (2026-11-24): đăng ký Tailscale hosted, ping trực tiếp 15 ms. **Board là node Tailscale chạy ổn định** (2026-11-24): UDP trực tiếp tới ~4 Mbit/s, phục hồi sau mất Wi‑Fi < 7 s, soak 30 phút 0 lỗi / không rò rỉ RAM ([docs/03 mục 9](docs/03-phase3-microlink-audit.md#9-bước-35-test-cuối-2026-11-24)). Còn: đo qua DERP, ACL chặn, mất gói 6 % khi soak, fuzz Noise/H2 |
| 4. Optimization | Streaming JSON, tối ưu RAM, soak test | Chưa bắt đầu |

## Cấu trúc

```
Makefile                       Điểm vào trên Linux/macOS: `make help`
tm.cmd                         Điểm vào trên Windows: `.\tm help` (cùng tên lệnh với make)
scripts/tm.py                  Toàn bộ logic setup/cấu hình/build/peer/đo (Python, chạy mọi OS)
scripts/install-deps.sh        `make deps`: gói apt (chỉ Linux)
config/secrets.env.example     FORM bí mật (Wi-Fi, khóa, IP tunnel, cổng serial)
config/project.env             Thiết lập không bí mật (phiên bản ESP-IDF, đường dẫn)
firmware/phase2-wg-poc/        ESP-IDF project: WireGuard tunnel + đo RAM/CPU/timing + dịch vụ benchmark
  components/esp_wireguard/    esp_wireguard 0.9.0 nhúng + vá (xem TAILMOTE_PATCHES.md)
tools/bench/                   wg_bench.py (client đo), serial_capture.py, parse_metrics.py
tests/                         Unit test cho tooling: `make test` / `.\tm test`
docs/                          Báo cáo và hướng dẫn từng phase
secrets/                       (git-ignored) secrets.env đã điền, khóa WireGuard, cấu hình peer
results/                       (git-ignored) log và kết quả đo
```

## Môi trường cần có

Linux, macOS và **Windows 10/11** đều được hỗ trợ; **không cần Linux** để build, nạp hay chạy test trên Windows.

| Thành phần | Linux (Ubuntu/Debian) | Windows 10/11 |
|---|---|---|
| Lệnh | `make <lệnh>` | `.\tm <lệnh>` (PowerShell/cmd, tại thư mục repo) |
| Có sẵn trước | `make`, Python 3.8+ | Git, Python 3.8+ (`winget install Git.Git Python.Python.3.12`) |
| Gói hệ thống | `make deps` (apt + sudo): flex, bison, gperf, cmake, ninja, ccache, python3-venv, wireguard-tools… | Không cần — `.\tm idf` tự tải cmake, ninja, toolchain |
| ESP-IDF **v5.3.6** | `make idf` → `~/esp/esp-idf-v5.3.6`, tools `~/.espressif` | `.\tm idf` → `C:\Espressif\frameworks\esp-idf-v5.3.6`, tools `C:\Espressif` |
| Driver board | có sẵn trong kernel; user thuộc nhóm `dialout` | CH340 (WCH CH341SER) hoặc CP210x (Silicon Labs) |
| Peer WireGuard (chỉ khi chạy) | `wg-quick` (wireguard-tools), sudo | [WireGuard for Windows](https://www.wireguard.com/install/) (`winget install WireGuard.WireGuard`), PowerShell **admin** |
| Đĩa / mạng | ~4 GB trống; Internet cho lần cài và lần build đầu (tải `trombik/esp_wireguard`) | như Linux |

Ghi chú Windows:
- ESP-IDF **không hỗ trợ dấu cách** trong `IDF_PATH`/`IDF_TOOLS_PATH`; mặc định `C:\Espressif` thỏa điều kiện. Đổi vị trí trong `config/project.env` (ví dụ `IDF_PATH='D:\Espressif\frameworks\esp-idf-v5.3.6'`).
- Khóa WireGuard được sinh bằng Python (X25519, RFC 7748) — không cần cài `wg` để chạy `config`.
- `.	m idf` lỗi `CERTIFICATE_VERIFY_FAILED` khi tải toolchain từ github.com: chạy lại qua mirror của Espressif: `$env:IDF_GITHUB_ASSETS='dl.espressif.com/github_assets'; .	m idf`.
- Cổng serial tự dò theo VID của CH34x/CP210x/FTDI/Espressif (bỏ qua cổng COM Bluetooth); đặt `SERIAL_PORT='COM7'` trong form nếu muốn cố định.

Thay thế: chỉ có Docker → `make docker-build` / `.\tm docker-build` (image `espressif/idf:v5.3.6`).

`make doctor` / `.\tm doctor` kiểm tra toàn bộ bảng trên và chỉ ra mục còn thiếu, không thay đổi gì.

## Setup & Build

Các lệnh dưới đây viết dạng `make <lệnh>`; trên Windows gõ `.\tm <lệnh>` (mỗi lệnh một lần, ví dụ `.\tm config` rồi `.\tm build`).

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

Trên Windows bỏ qua `deps`; `.\tm deps` chỉ in danh sách cần có.

### 2. Điền form bí mật

```bash
make secrets
```

Mở `secrets/secrets.env` và điền. Bắt buộc: `WIFI_SSID`, `WIFI_PASSWORD`. Các mục khác có mặc định; để trống khóa thì script tự sinh.

| Mục | Nội dung |
|---|---|
| [1] Wi‑Fi | SSID, mật khẩu (2.4 GHz, WPA2, 8–63 ký tự hoặc trống) |
| [2] Peer | `WG_ENDPOINT` (trống = IP LAN tự động của máy chạy `config`), `WG_PORT` |
| [3] Tunnel | IP peer / ESP32, netmask, prefix |
| [4] Khóa | Khóa riêng ESP32 / peer (trống = tự sinh), preshared key (`auto` = tự sinh) |
| [5] Tunnel | Keepalive, Wi‑Fi power save (`none` / `min_modem`) |
| [6] Nạp | Cổng serial (trống = tự dò; ví dụ `/dev/ttyUSB0`, `COM7`), baud |

```bash
make config
```

`make config` kiểm tra form (báo lỗi từng mục), rồi sinh `secrets/*.key|pub`, `secrets/wg-esp.conf` (cấu hình peer, dùng được cho cả `wg-quick` và WireGuard for Windows) và `firmware/phase2-wg-poc/sdkconfig.secrets`. Chạy lại an toàn: khóa đã sinh được giữ nguyên.

### 3. Build

```bash
make build
```

```bash
make size
```

`make build` tự tạo lại `sdkconfig` khi `sdkconfig.defaults` hoặc `sdkconfig.secrets` thay đổi (thay đổi riêng qua `make menuconfig` sẽ mất khi đó).

Unit test cho tooling (không cần board hay ESP-IDF):

```bash
make test
```

## Run & Đo (khi có board)

Máy chạy peer (Linux, hoặc Windows với PowerShell **Run as administrator**) phải cùng LAN với ESP32. Trên Windows, `peer-up` cài tunnel `wg-esp` vào WireGuard for Windows và mở UDP `WG_PORT` trên Windows Firewall (rule `tailmote-wg-esp`); `peer-down` gỡ cả hai.

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

Trên Windows: `.\tm bench --host <ESP32 LAN IP>` (và `--label "..."`).

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

1. ~~**`esp_wireguard` 0.9.0 với ESP-IDF v5.3**~~: **đã xác nhận** build và chạy được với IDF v5.3.6. Cần `CONFIG_ESP_NETIF_BRIDGE_EN=y` + `CONFIG_LWIP_NUM_NETIF_CLIENT_DATA=1` để tránh panic khi tạo netif (đã có trong `sdkconfig.defaults`, lý do ở docs/02 mục 6.1).
2. ~~**Tên Kconfig** trong `sdkconfig.defaults`~~ — **đã xác nhận**: cả 20 tùy chọn có trong `sdkconfig` sau build.
3. **Chip revision** từ sự kiện `boot` (`rev` = major×100 + minor). Rev < 300 không có Secure Boot V2. Board hiện tại: `rev` = 100 (ESP32-D0WD v1.0) → chỉ dùng cho nghiên cứu.
4. **Stack task**: `wg_sup` đã tăng 4 096 → 8 192 B (overflow ở lần connect đầu). `tiT` còn 660 B khi tải nhẹ, cần theo dõi; tăng nếu `stack_free_min` < 512 B.
5. **Thread safety** khi reconnect: `esp_wireguard_connect/disconnect` gọi từ task `wg_sup` như example của thư viện.
6. **SNTP** (`pool.ntp.org`, UDP 123) phải thành công trước handshake.

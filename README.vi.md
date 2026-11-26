# esp32-tailscale

[English](README.md) · **Tiếng Việt**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![ESP-IDF v5.3.6](https://img.shields.io/badge/ESP--IDF-v5.3.6-red.svg)
![Target: ESP32](https://img.shields.io/badge/target-ESP32%20(no%20PSRAM)-green.svg)
![Platforms: Linux | macOS | Windows](https://img.shields.io/badge/host-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)

Dự án này đưa **ESP32 vào tailnet như một node Tailscale thực thụ**: không cần Linux
gateway, không cần PSRAM ngoài. Board tự đăng ký với control server và nhận netmap. Nó
kết nối thẳng tới các peer bằng NAT traversal (DISCO/STUN), và chuyển sang relay DERP
khi không đi thẳng được.

Phần cứng tham chiếu: **ESP32 DOIT DevKit V1** (ESP32-D0WD rev 1.0, 520 KB SRAM,
4 MB flash, **không PSRAM**). ESP8266 nằm ngoài phạm vi vì không đủ RAM (xem báo cáo
Phase 1).

> [!IMPORTANT]
> Đây là dự án nghiên cứu, chưa phải sản phẩm đã gia cố để dùng thật. Firmware **chưa**
> bật mã hóa flash và NVS, nên khóa thiết bị và auth key Tailscale nằm không mã hóa
> trên chip. Đọc [Lưu ý bảo mật](#lưu-ý-bảo-mật) trước khi đặt board ở nơi bạn không
> kiểm soát.

---

## Mục lục

- [Điểm nổi bật](#điểm-nổi-bật)
- [Trạng thái dự án](#trạng-thái-dự-án)
- [Cách hoạt động](#cách-hoạt-động)
- [Cấu trúc repo](#cấu-trúc-repo)
- [Môi trường cần có](#môi-trường-cần-có)
- [Bắt đầu nhanh](#bắt-đầu-nhanh)
- [Danh sách lệnh](#danh-sách-lệnh)
- [Phase 2: WireGuard PoC và đo đạc](#phase-2-wireguard-poc-và-đo-đạc)
- [Kiểm thử](#kiểm-thử)
- [Lưu ý bảo mật](#lưu-ý-bảo-mật)
- [Giới hạn đã biết](#giới-hạn-đã-biết)
- [Xử lý sự cố](#xử-lý-sự-cố)
- [Giấy phép](#giấy-phép)

## Điểm nổi bật

Số đo trên board tham chiếu, dùng control server Tailscale chính thức, tailnet 4 máy
([docs/03 mục 9–10](docs/03-phase3-microlink-audit.md#9-bước-35-test-cuối-2026-11-24)):

| Chỉ số | Kết quả |
|---|---|
| Thời gian từ boot tới `CONNECTED` | **9,8 s** (DERP region nhà là Singapore) |
| Thông lượng UDP, đường trực tiếp | tới **~4 Mbit/s**; mất 0 % ở mức ≤ 2 Mbit/s |
| RTT đường trực tiếp (gói 64 B) | p50 22 ms, p95 100 ms |
| Phục hồi sau khi mất Wi-Fi | **< 7 s** kể từ khi có mạng lại |
| Soak 30 phút | 0 lần reboot, 0 lần cấp phát lỗi, 0 lần kết nối lại |
| Heap trống khi soak | ổn định ~68 KB (thấp nhất 40,5 KB), không rò rỉ |
| CPU | core 0: 2,9 %, core 1: 1,2 % |
| Ảnh firmware | ~966 KB (`--prod`), phân vùng app còn trống 51 % |

## Trạng thái dự án

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 1. Khả thi | [docs/01-feasibility-report.md](docs/01-feasibility-report.md) | ✅ Xong: **GO có điều kiện** cho DOIT DevKit V1 |
| 2. WireGuard PoC | [docs/02-phase2-wireguard-poc.md](docs/02-phase2-wireguard-poc.md), [`firmware/phase2-wg-poc`](firmware/phase2-wg-poc) | ✅ Xong (2026-11-24): tunnel WireGuard thuần chạy trên board thật |
| 3. Tích hợp Tailscale | [docs/03-phase3-microlink-audit.md](docs/03-phase3-microlink-audit.md), [`firmware/phase3-tailscale`](firmware/phase3-tailscale) | ✅ Xong (2026-11-24): board là node Tailscale, có profile build cho bản dùng thật |
| 4. Tối ưu | Parser JSON dạng streaming, tối ưu RAM, soak 24 h | ⏳ Chưa bắt đầu |

Chưa kiểm chứng: đường DERP từ máy ngoài LAN, policy ACL có chặn, soak 24 giờ, fuzz
tự động lớp Noise/HTTP/2, mã hóa flash/NVS.

## Cách hoạt động

```
                 ┌───────────────── ESP32 (không PSRAM) ──────────────┐
  Control        │  main (tailmote_phase3)  ── METRIC telemetry ──► UART │
  server   ◄──── │  MicroLink v2.3.0 (nhúng sẵn + 8 bản vá)            │
  Tailscale      │    ├─ client điều khiển ts2021 / Noise IK + HTTP/2  │
  (ts2021) Noise │    ├─ bộ cắt MapResponse (vừa netmap trong SRAM)    │
                 │    ├─ NAT traversal DISCO / STUN ───────────────────┼──► peer (UDP trực tiếp)
  Relay DERP ◄── │    ├─ client relay DERP ───────────────────────────┼──► peer (qua relay)
                 │    └─ wireguard-lwip  →  lwIP  →  esp_wifi          │
                 └─────────────────────────────────────────────────────┘
```

Firmware Phase 3 dựa trên [MicroLink](https://github.com/CamM2325/microlink). Thư viện
được nhúng sẵn trong
[`components/microlink`](firmware/phase3-tailscale/components/microlink) cùng 8 bản vá.
Các bản vá giúp nó vừa RAM trong chip, sửa lỗi mất netmap và sửa 5 lỗi mà fuzz tìm ra
trong bộ cắt JSON. Từng bản vá được ghi trong
[`TAILMOTE_PATCHES.md`](firmware/phase3-tailscale/components/microlink/TAILMOTE_PATCHES.md).

## Cấu trúc repo

```
Makefile                      Điểm vào trên Linux/macOS (`make help`)
tm.cmd                        Điểm vào trên Windows (`.\tm help`), cùng tên lệnh
scripts/tm.py                 Toàn bộ logic setup/cấu hình/build/nạp/peer/đo (Python 3.8+, chỉ thư viện chuẩn)
scripts/install-deps.sh       Gói hệ thống cho Linux (`make deps`)
config/project.env            Thiết lập không bí mật: phiên bản ESP-IDF, đường dẫn, project firmware
config/secrets.env.example    Form bí mật: Wi-Fi, khóa WireGuard, auth key Tailscale, cổng serial
firmware/
  phase2-wg-poc/              Project ESP-IDF: tunnel WireGuard + đo RAM/CPU/timing + dịch vụ benchmark
    components/esp_wireguard/ esp_wireguard 0.9.0, nhúng sẵn và có vá
  phase3-tailscale/           Project ESP-IDF: node Tailscale (build mặc định)
    components/microlink/     MicroLink v2.3.0, nhúng sẵn và có vá
    sdkconfig.prod.defaults   Overlay cho bản dùng thật (`tm build --prod`)
    test/                     Unit test trên host và bộ fuzz cho bộ cắt JSON
tools/bench/                  Client đo hiệu năng, phục hồi, mất gói; ghi serial; phân tích metric
tests/                        Unit test cho tooling (`make test`)
docs/                         Báo cáo, số đo và quyết định thiết kế của từng phase
secrets/                      (git-ignored) form đã điền, khóa đã sinh, cấu hình peer
results/                      (git-ignored) log serial và kết quả đo
```

## Môi trường cần có

Máy build có thể là Linux, macOS hoặc **Windows 10/11**. Trên Windows không cần WSL hay
máy Linux.

| | Linux (Ubuntu/Debian) | Windows 10/11 |
|---|---|---|
| Cách gọi lệnh | `make <lệnh>` | `.\tm <lệnh>` (PowerShell hoặc cmd, tại thư mục repo) |
| Cần có sẵn | `make`, Python 3.8+ | Git, Python 3.8+ (`winget install Git.Git Python.Python.3.12`) |
| Gói hệ thống | `make deps` (apt, cần sudo) | Không cần: `.\tm idf` tự tải cmake, ninja và toolchain |
| ESP-IDF **v5.3.6** | `make idf` → `~/esp/esp-idf-v5.3.6`, tools ở `~/.espressif` | `.\tm idf` → `C:\Espressif\frameworks\esp-idf-v5.3.6`, tools ở `C:\Espressif` |
| Driver USB-serial | Có sẵn trong kernel; user phải thuộc nhóm `dialout` | CH340 (WCH CH341SER) hoặc CP210x (Silicon Labs) |
| Peer WireGuard (chỉ Phase 2) | `wireguard-tools`, sudo | [WireGuard for Windows](https://www.wireguard.com/install/), PowerShell quyền admin |
| Ổ đĩa | trống ~4 GB | trống ~4 GB |

Nếu chỉ có Docker, dùng `make docker-build` (hoặc `.\tm docker-build`). Lệnh này build
bằng image `espressif/idf:v5.3.6`.

`make doctor` kiểm tra mọi dòng trong bảng trên và báo những gì còn thiếu. Lệnh này
không thay đổi gì.

## Bắt đầu nhanh

Các bước dưới đây build và chạy **node Tailscale (Phase 3)**. Trên Windows, thay
`make <lệnh>` bằng `.\tm <lệnh>`.

### 1. Cài toolchain (một lần)

```bash
make doctor
```

```bash
make deps
```

```bash
make idf
```

Trên Windows bỏ qua `deps`.

### 2. Điền form bí mật

```bash
make secrets
```

Lệnh này tạo `secrets/secrets.env` (đã git-ignore). Tối thiểu cần điền các mục sau:

| Mục | Ý nghĩa |
|---|---|
| `WIFI_SSID`, `WIFI_PASSWORD` | Mạng 2.4 GHz, WPA2 |
| `TS_AUTH_KEY` | Tạo ở [trang admin Tailscale](https://login.tailscale.com/admin/settings/keys) (nên chọn *Reusable* + *Pre-approved*). Nếu để trống, board sẽ in URL đăng nhập ra cổng serial. |
| `TS_DEVICE_NAME` | Tên máy trên tailnet (mặc định `tailmote-esp32`) |
| `TS_DERP_REGION` | ID region DERP nhà. Chọn region gần nhất bằng `tailscale netcheck`, ví dụ `3` = Singapore, `20` = Hong Kong, `7` = Tokyo. Nếu để trống, MicroLink dùng region `9` (Dallas). |
| `SERIAL_PORT` | Nên đặt nếu máy cắm nhiều adapter USB-serial, ví dụ `/dev/serial/by-id/usb-1a86_…` hoặc `COM7`. Nếu để trống, script dùng cổng đầu tiên tìm thấy. |

Sau đó sinh cấu hình build:

```bash
make config
```

`config` kiểm tra form, rồi ghi `sdkconfig.secrets` cho cả hai firmware. Nó cũng sinh
khóa WireGuard và cấu hình peer dùng cho Phase 2. Chạy lại nhiều lần vẫn an toàn: khóa
đã có được giữ nguyên.

### 3. Build

```bash
make build
```

Build cho bản dùng thật (tắt test hook, log mức `WARN`, vẫn giữ dòng `METRIC`):

```bash
python3 scripts/tm.py build --prod
```

Trên Windows: `.\tm build --prod`. `make build` không truyền tham số `--prod`.

`build` tự tạo lại `sdkconfig` mỗi khi `sdkconfig.defaults`, `sdkconfig.secrets` hoặc
profile thay đổi. Khi đó, mọi thay đổi bạn làm qua `menuconfig` sẽ mất.

### 4. Nạp và xem log

```bash
make flash-monitor
```

Theo dõi log tới khi MicroLink báo `CONNECTED`. Khi đó board hiện lên trong trang admin
Tailscale, và mọi peer trong tailnet có thể gọi tới nó qua địa chỉ `100.x.y.z`. Nhấn
`Ctrl+]` để thoát monitor.

## Danh sách lệnh

`make help` (Linux/macOS) và `.\tm help` (Windows) in ra danh sách này.

| Lệnh | Mô tả |
|---|---|
| `doctor` | Kiểm tra môi trường, không thay đổi gì |
| `deps` | Cài gói hệ thống (Linux: apt + sudo; Windows: chỉ in hướng dẫn) |
| `idf` | Clone và cài ESP-IDF cùng toolchain esp32 |
| `secrets` | Tạo `secrets/secrets.env` từ form (không bao giờ ghi đè) |
| `config` | Kiểm tra form; sinh khóa, cấu hình peer và `sdkconfig.secrets` |
| `build` | Build firmware (`--prod` cho bản dùng thật) |
| `size` | Báo cáo Flash/RAM tĩnh → `results/` |
| `flash` / `monitor` / `flash-monitor` | Nạp firmware, mở serial monitor, hoặc cả hai |
| `menuconfig` / `clean` / `fullclean` | Các lệnh `idf.py` thông thường (`fullclean` xóa luôn `sdkconfig`) |
| `docker-build` | Build trong image `espressif/idf` |
| `peer-up` / `peer-down` / `peer-status` | Quản lý peer WireGuard của Phase 2 trên máy này |
| `log` | Reset board và ghi log serial → `results/serial.log` |
| `bench` | Chạy bộ đo của Phase 2 (`--host <ip>` để đo baseline LAN, `--label …`) |
| `metrics` | Tóm tắt `results/serial.log` → `results/periodic.csv` |
| `fuzz` | Fuzz bộ cắt JSON của MicroLink trên host (`--seconds N`, `--seed S`) |
| `test` | Unit test cho tooling và test trên host (không cần board hay ESP-IDF) |

`fuzz` không có target `make`. Chạy bằng `python3 scripts/tm.py fuzz` (hoặc `.\tm fuzz`).

Project firmware mặc định là Phase 3. Muốn build PoC Phase 2 thì đặt `FW_PROJECT`:

```bash
FW_PROJECT=firmware/phase2-wg-poc make build
```

Trên Windows: `$env:FW_PROJECT='firmware/phase2-wg-poc'; .\tm build`.

## Phase 2: WireGuard PoC và đo đạc

Phase 2 dựng một tunnel WireGuard thuần giữa ESP32 và một peer trong cùng LAN. Mục đích
là đo chi phí RAM, CPU và thời gian của riêng WireGuard, trước khi thêm logic Tailscale.
Máy chạy peer phải cùng LAN với board. Trên Windows cần mở PowerShell quyền admin:
`peer-up` cài tunnel `wg-esp` và mở cổng UDP `WG_PORT` trên Windows Firewall, còn
`peer-down` gỡ cả hai.

```bash
make peer-up
```

```bash
FW_PROJECT=firmware/phase2-wg-poc make flash-monitor
```

Log cần thấy lần lượt: `METRIC {"ev":"boot"…}`, rồi `wifi_got_ip`, `sntp_synced` và
cuối cùng `wg_up`. Sau đó `make peer-status` phải có dòng `latest handshake`.

```bash
make log
```

```bash
make bench
```

```bash
make bench HOST=<IP LAN của ESP32>
```

```bash
make metrics
```

```bash
make peer-down
```

Chạy `make log` ở một terminal riêng trong lúc đo. `make bench HOST=…` đo baseline
không qua tunnel. Ma trận đo (M1–M13), các kịch bản phục hồi và kết quả nằm trong
[docs/02](docs/02-phase2-wireguard-poc.md).

## Kiểm thử

| Lớp | Cách chạy |
|---|---|
| Tooling (`tm.py`: đọc env, kiểm tra form, sinh khóa X25519 đối chiếu vector RFC 7748) | `make test` |
| Bộ cắt JSON của MicroLink, build trên host | `make test` (qua `tests/test_json_prune_c.py`) |
| Fuzz bộ cắt JSON | `python3 scripts/tm.py fuzz --seconds 60` |
| Trên board | Telemetry `METRIC` ở cổng serial, cùng `tools/bench/ts_recovery.py` và `ts_lossprobe.py` qua tailnet |

## Lưu ý bảo mật

- **Chưa mã hóa flash hay NVS.** Khóa máy, khóa node, khóa disco và auth key nằm dạng
  rõ trong NVS và trong ảnh firmware. Profile `--prod` cố ý **không** bật mã hóa: bật
  sẽ đốt eFuse ở lần boot đầu và không đảo ngược được. Ngoài ra chip rev 1.0 chỉ có
  Secure Boot V1. Khi làm phần cứng sản phẩm, hãy bật có chủ đích theo hướng dẫn flash
  encryption của ESP-IDF.
- **Coi board đã nạp như một thông tin đăng nhập.** Dùng auth key có tag, đã
  pre-approve, với quyền ACL tối thiểu. Nếu mất board, hãy thu hồi node trong trang
  admin.
- **Phần xử lý dữ liệu trước khi xác thực đã được rà soát tay**
  ([docs/03 mục 10.2](docs/03-phase3-microlink-audit.md#102-rà-soát-tay-phần-nhận-trước-khi-xác-thực)).
  Không thấy lỗi an toàn bộ nhớ. Kẻ tấn công trên đường truyền có thể làm lệch nonce
  nhận để buộc kết nối lại, nhưng không đọc hay sửa được dữ liệu.
- **Test hook** (`CONFIG_TM_TEST_HOOKS`, ví dụ `WIFI_DOWN <s>`) bật trong bản build mặc
  định và bị tắt bởi `--prod`. Không đem bản không phải `--prod` đi dùng thật.
- Không bao giờ commit nội dung trong `secrets/` hay bất kỳ file `sdkconfig.secrets`
  nào. Cả hai đều đã được git-ignore.

## Giới hạn đã biết

- Tối đa khoảng **8 peer**, cho tới khi bộ cắt netmap được thay bằng parser JSON dạng
  streaming (Phase 4).
- Dịch vụ trên board mới chỉ có UDP. Thông lượng TCP chưa được đo.
- Board gửi *endpoint update* khoảng 23 s một lần dù endpoint không đổi. Việc này
  không gây lỗi nhưng tốn traffic và điện (Phase 4).
- Board tham chiếu là ESP32-D0WD rev 1.0, chỉ phù hợp cho nghiên cứu. Cần chip
  rev ≥ 3.0 để có Secure Boot V2.

## Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `make doctor` báo thiếu thành phần | Chạy lệnh mà nó gợi ý (`make deps`, `make idf`, `make config`) |
| Build cảnh báo *"no sdkconfig.secrets"* | Chạy `make config`. Thiếu file này, firmware không có thông tin Wi-Fi và Tailscale |
| Nạp nhầm vào thiết bị khác | Đặt `SERIAL_PORT` trong `secrets/secrets.env`. Trên Linux nên dùng đường dẫn `/dev/serial/by-id/…` |
| `Permission denied` khi mở `/dev/ttyUSB*` | Thêm user vào nhóm `dialout` (`sudo usermod -aG dialout $USER`), rồi đăng nhập lại |
| Windows: `CERTIFICATE_VERIFY_FAILED` khi chạy `.\tm idf` | `$env:IDF_GITHUB_ASSETS='dl.espressif.com/github_assets'; .\tm idf` |
| Windows: ESP-IDF lỗi vì đường dẫn có dấu cách | Đổi `IDF_PATH` / `IDF_TOOLS_PATH` trong `config/project.env` sang đường dẫn không có dấu cách |
| Lâu mới `CONNECTED`, hoặc relay trễ cao | Đặt `TS_DERP_REGION` là region gần bạn nhất |
| Board in ra URL đăng nhập | `TS_AUTH_KEY` đang trống hoặc đã hết hạn. Mở URL để duyệt, hoặc đặt key mới rồi chạy `make config build flash` |

## Giấy phép

Dự án dùng giấy phép [Apache-2.0](LICENSE); xem thêm [NOTICE](NOTICE). Mỗi file mã
nguồn đều có header `SPDX-License-Identifier`. Các thành phần bên thứ ba (ESP-IDF,
esp_wireguard, wireguard-lwip, MicroLink, …) giữ giấy phép gốc, liệt kê trong
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Nếu phân phối file firmware, bạn phải
kèm theo các thông báo bản quyền đó.

Đây là dự án độc lập, không liên kết hay được bảo trợ bởi Tailscale Inc. hay Espressif
Systems. "Tailscale" và "WireGuard" là nhãn hiệu của chủ sở hữu tương ứng.

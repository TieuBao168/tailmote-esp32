# Phase 2 — WireGuard PoC trên ESP32 DOIT DevKit V1

> **Trạng thái (2026-11-23):** đã có code và kế hoạch đo. **Chưa build, chưa chạy trên phần cứng** (chưa có module). Các bước build/run tóm tắt trong [README.md](../README.md#build--run).

Mục tiêu: chạy WireGuard trên board, nối với một peer Linux, và **thay các số ước lượng [E] trong báo cáo Phase 1 bằng số đo thật**: RAM, Flash, CPU, thời gian kết nối, throughput, latency, packet loss, thời gian phục hồi.

> Phase 2 là **WireGuard-compatible**, chưa phải Tailscale-compatible. Peer Linux ở đây là WireGuard thuần (`wg-quick`), không phải tailscaled.

## 1. Cấu trúc

```
firmware/phase2-wg-poc/        ESP-IDF project (target esp32)
  main/main.c                  boot, chip info, khởi động các module
  main/wifi.c                  Wi-Fi STA, reconnect backoff 0.5 s → 30 s
  main/wg_tunnel.c             SNTP → esp_wireguard connect → giám sát, tự kết nối lại
  main/metrics.c               dòng METRIC: heap, block lớn nhất, CPU% từng core, stack task
  main/bench.c                 UDP echo :7, UDP sink :5001, TCP sink :5002, TCP source :5003
  partitions.csv               4 MB, 2×OTA 1.875 MB, coredump
  sdkconfig.defaults           BT tắt, 240 MHz, FreeRTOS stats, tcpip trên core 1
  sdkconfig.secrets            (git-ignored) Wi-Fi + khóa WireGuard
config/secrets.env.example     form bí mật → make secrets / make config
scripts/                       setup, sinh cấu hình, build, peer (gọi qua Makefile)
tools/bench/wg_bench.py        client đo RTT/loss/throughput (chạy trên Linux)
tools/bench/serial_capture.py  ghi log serial có timestamp
tools/bench/parse_metrics.py   tóm tắt log METRIC → JSON/CSV
```

Thư viện WireGuard: [`trombik/esp_wireguard`](https://components.espressif.com/components/trombik/esp_wireguard) 0.9.0 (BSD-3-Clause), lấy tự động qua ESP Component Manager. README của nó chỉ liệt kê IDF ≤ 4.4 + master, nhưng CMake có nhánh riêng cho IDF 5 — **khả năng tương thích với IDF v5.3 là một điểm cần xác nhận khi build**.

## 2–4. Môi trường, form bí mật, build

Xem [README.md — Setup & Build](../README.md#setup--build). Tóm tắt:

```bash
make deps idf secrets
```

Điền `secrets/secrets.env`, rồi:

```bash
make config build size
```

ESP32 và máy Linux phải cùng mạng hoặc máy Linux phải nhận được UDP `WG_PORT` từ ESP32.

## 5. Chạy và đo

```bash
make peer-up flash-monitor
```

Kỳ vọng: `boot` → `wifi_got_ip` → `sntp_synced` → `wg_up`; `make peer-status` có `latest handshake`.

Terminal 1 ghi log, terminal 2 chạy đo:

```bash
make log
```

```bash
make bench
```

**Baseline không tunnel** (cùng firmware, cùng dịch vụ, qua IP LAN của ESP32 trong sự kiện `wifi_got_ip`) — chênh lệch giữa hai lần chạy chính là chi phí WireGuard:

```bash
make bench HOST=<ESP32 LAN IP>
```

Tóm tắt phía thiết bị (RAM, CPU, stack, timeline, recovery):

```bash
make metrics
```

### Ma trận đo

| # | Thông số | Cách đo | Nguồn số liệu |
|---|---|---|---|
| M1 | Flash | `idf.py size`, `size-components` (phần `libesp_wireguard.a`) | build |
| M2 | RAM tĩnh | `idf.py size` (DRAM/IRAM) | build |
| M3 | RAM động của WireGuard | `heap_delta` `wg_connect`, `wg_session` | METRIC |
| M4 | Heap trống / block liền kề lớn nhất / watermark | `periodic` | METRIC |
| M5 | Stack thật của từng task | `task.stack_free_min` | METRIC |
| M6 | CPU% core 0/1 khi rảnh và khi tải | `periodic.cpu0/cpu1` trong lúc chạy `wg_bench.py` | METRIC |
| M7 | Thời gian kết nối | `boot_timeline_ms`, `handshake_ms` | METRIC |
| M8 | Latency, jitter, loss | `rtt` 64 B và 1200 B | wg_bench |
| M9 | Throughput | `udp-rx` 1/2/4/8 Mbit/s, `tcp-rx`, `tcp-tx` | wg_bench + METRIC |
| M10 | Phục hồi sau mất mạng | Tắt AP / rút WAN 30 s, 5 phút; `recovery_ms` | METRIC |
| M11 | Ảnh hưởng power save | Build lại với `POC_WIFI_PS_MIN_MODEM`, chạy lại M6, M8, M9 | cả hai |
| M12 | Soak test | 24–72 h, `rtt --count 100000 --interval 1` song song serial log | cả hai |
| M13 | Dòng tiêu thụ | INA219 / USB power meter; ghi rõ đo tại USB 5 V hay 3V3 module | thiết bị đo |

### Kịch bản phục hồi (M10)

1. Tunnel đang lên, chạy `wg_bench.py rtt --count 100000 --interval 1`.
2. Tắt Wi‑Fi AP (hoặc đổi mật khẩu tạm) 30 s → bật lại. Lặp với 5 phút.
3. Khởi động lại `wg-quick` trên Linux (peer mất session) → board phải tự handshake lại (keepalive 25 s, restart tunnel sau 60 s peer down).
4. Rút nguồn board → sau boot phải lên lại (kiểm tra SNTP trước handshake; nếu thiếu, Linux sẽ từ chối initiation có timestamp cũ).

## 6. Bảng kết quả (điền sau khi đo)

| Thông số | Giá trị | Ghi chú |
|---|---|---|
| Chip revision | | từ sự kiện `boot` |
| Flash firmware / phần WireGuard | | |
| DRAM tĩnh | | |
| Heap trống sau Wi‑Fi / sau tunnel | | |
| RAM WireGuard (connect + session) | | |
| Block liền kề lớn nhất (min) | | |
| CPU rảnh / khi tải (core 0 / core 1) | | |
| Boot → Wi‑Fi IP / → SNTP / → tunnel | | |
| RTT 64 B tunnel vs LAN (p50 / p95) | | |
| UDP goodput tối đa, loss < 1 % | | |
| TCP RX / TX | | |
| Recovery sau mất Wi‑Fi 30 s / 5 phút | | |
| Soak 24 h: reboot, heap min | | |

## 7. Tiêu chí hoàn thành Phase 2

- Tunnel lên ổn định, `ping 10.66.0.2` từ Linux.
- Điền đủ bảng mục 6 cho cấu hình PS none; có ít nhất M6, M8, M9 cho PS min-modem.
- Không reboot, heap không giảm dần trong soak test 24 h.
- Cập nhật các mục **[E]** trong `docs/01-feasibility-report.md` (4.1, 4.4, 4.5) bằng số đo.

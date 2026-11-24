# Phase 2 — WireGuard PoC trên ESP32 DOIT DevKit V1

> **Trạng thái (2026-11-23):** đã có code và kế hoạch đo. **Chưa build, chưa chạy trên phần cứng** (chưa có module). Các bước build/run tóm tắt trong [README.md](../README.md#build--run).

Mục tiêu: chạy WireGuard trên board, nối với một peer Linux, và **thay các số ước lượng [E] trong báo cáo Phase 1 bằng số đo thật**: RAM, Flash, CPU, thời gian kết nối, throughput, latency, packet loss, thời gian phục hồi.

> Phase 2 là **WireGuard-compatible**, chưa phải Tailscale-compatible. Peer ở đây là WireGuard thuần (`wg-quick` trên Linux hoặc WireGuard for Windows), không phải tailscaled.

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
scripts/tm.py                  setup, sinh cấu hình, build, peer (Linux: make, Windows: .	m)
tools/bench/wg_bench.py        client đo RTT/loss/throughput (chạy trên máy peer)
tools/bench/serial_capture.py  ghi log serial có timestamp
tools/bench/parse_metrics.py   tóm tắt log METRIC → JSON/CSV
```

Thư viện WireGuard: [`trombik/esp_wireguard`](https://components.espressif.com/components/trombik/esp_wireguard) 0.9.0 (BSD-3-Clause), lấy tự động qua ESP Component Manager. README của nó chỉ liệt kê IDF ≤ 4.4 + master, nhưng CMake có nhánh riêng cho IDF 5 — **khả năng tương thích với IDF v5.3 là một điểm cần xác nhận khi build**.

## 2–4. Môi trường, form bí mật, build

Xem [README.md — Setup & Build](../README.md#setup--build). Trên Windows thay `make <x>` bằng `.	m <x>` (mỗi lệnh một lần). Tóm tắt:

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
3. Khởi động lại peer (`make peer-down peer-up`) (peer mất session) → board phải tự handshake lại (keepalive 25 s, restart tunnel sau 60 s peer down).
4. Rút nguồn board → sau boot phải lên lại (kiểm tra SNTP trước handshake; nếu thiếu, Linux sẽ từ chối initiation có timestamp cũ).

## 6. Kết quả đo — lần chạy 1 (2026-11-24)

**Cấu hình:** ESP32-D0WD **rev v1.0** (DOIT DevKit V1, CH340, 4 MB flash), ESP-IDF v5.3.6, `esp_wireguard` 0.9.0, Wi‑Fi PS `none`, build và nạp trên **Windows 11** (`.\tm`), peer là **WireGuard for Windows** 1.1.1 trên cùng LAN. ESP32 ở 2.4 GHz, kênh 1/4, **RSSI −53 … −77 dBm**, mạng mesh có 3 BSSID và board chuyển AP nhiều lần. PC nối Wi‑Fi 5 GHz. Số liệu lấy từ log `METRIC` và `wg_bench.py`.

> **Lưu ý quan trọng:** đường Wi‑Fi của ESP32 trong lần chạy này **không ổn định**: RSSI yếu, chuyển AP, có lúc mất gói hàng loạt. Baseline không tunnel cũng chậm và mất gói gần như tunnel, nên số throughput/latency dưới đây là **giới hạn của môi trường RF**, chưa phải giới hạn của WireGuard trên ESP32. Phải đo lại khi đặt board gần AP / dùng AP 2.4 GHz cố định.

| Thông số | Giá trị | Ghi chú |
|---|---|---|
| Chip revision | `rev` = 100 (v1.0) | chỉ Secure Boot V1 → nghiên cứu, không dùng cho sản phẩm |
| Flash firmware / phần WireGuard | app 793 168 B (partition app còn 60 % trống); `libtrombik__esp_wireguard.a` 21 339 B (Flash 20 386 B, DRAM 953 B) | `tm size` |
| DRAM tĩnh / IRAM | DRAM 47 636 B (26 %); IRAM 95 662 B (73 %) | |
| Heap trống sau Wi‑Fi / sau tunnel | ~188 KB (`wifi_got_ip`) / ~186 KB (`wg_up`) | khi rảnh |
| RAM WireGuard (connect + session) | 1 348 B + 1 560 B ≈ **2.9 KB** | `heap_delta` |
| Block liền kề lớn nhất (min) | **110 592 B** | không đổi suốt lần chạy |
| Heap min khi tải | 140 024 B | `heap_min` sau các bài UDP/TCP |
| CPU rảnh / khi tải (core 0 / core 1) | 0.4–1.9 % / 0.1–0.6 %; khi tải tối đa 4.6 % / **23.6 %** | crypto chạy trong `tiT` (core 1); CPU không phải điểm nghẽn |
| Boot → Wi‑Fi IP / → SNTP / → tunnel | 1.5–3.0 s / 4.8–6.5 s / **7.7 s** (handshake 1 160 ms) | SNTP tốn 0.4–5 s, có lần timeout 20 s |
| RTT 64 B tunnel vs LAN (p50 / p95) | tunnel 19 / 157 ms (lúc tốt) … 135 / 396 ms; LAN 18 / 335 ms | dao động theo chất lượng Wi‑Fi, không tách được chi phí WireGuard |
| UDP goodput, loss < 1 % | LAN: 1.07 Mbit/s tại 2 Mbit/s offered, loss 0 %; tunnel: chưa đạt loss < 1 % (64–94 % ở các lần chạy) | các lần đo không cùng thời điểm, xem lưu ý |
| TCP RX / TX (PC→ESP / ESP→PC) | tunnel **0.66 / 0.83 Mbit/s**, LAN **0.81 / 0.88 Mbit/s** (cùng thời điểm); lần trước tunnel 0.32 / 1.95 | tunnel chậm hơn LAN 5–20 % |
| Stack còn trống thấp nhất | `tiT` **660 B** (stack 5 120 B), `sys_evt` 688 B, `b_usink` 900 B, `wg_sup` 3 360 B (stack 8 KB) | đã tăng `tiT` lên 6 144 B → còn 1 684 B (6.3) |
| Recovery sau mất Wi‑Fi 30 s / 5 phút | chưa đo (cần tắt AP thủ công) | M10 |
| Recovery sau khi peer tắt 30 s / 120 s | tunnel thông lại **71 s** / **5 s** sau khi peer bật lại | M10, xem 6.3: firmware không phát hiện peer mất |
| Soak 24 h: reboot, heap min | chưa đo; 12 phút liên tục có tải: 0 reboot | M12 |
| Power save `min_modem` | RTT 64 B p50/p95 **155 / 367 ms**, 1200 B 222 / 473 ms, 0 % loss; TCP TX 0.17 Mbit/s; CPU rảnh 2–3 % / 0.5 % | M11, cùng điều kiện RF xấu như trên; UDP/TCP RX lỗi timeout |

### 6.1. Lỗi gặp ở lần chạy đầu và cách sửa

1. **Panic `LoadProhibited` trong `esp_wireguard_connect` → `netif_add`.** Ở IDF 5.x, `esp_netif` đăng ký ext-callback của lwIP cho *mọi* netif. Nó đọc `netif->state` như `esp_netif_t*`, trong khi netif WireGuard đặt struct riêng vào `netif->state`, nên callback deref sai và panic. Sửa trong `sdkconfig.defaults`: `CONFIG_ESP_NETIF_BRIDGE_EN=y` + `CONFIG_LWIP_NUM_NETIF_CLIENT_DATA=1`. Khi đó IDF bật `LWIP_ESP_NETIF_DATA`, lưu `esp_netif` vào `netif->client_data` và bỏ qua netif lạ. Bridge không được dùng; firmware tăng ~500 B.
2. **Stack overflow ở task `wg_sup`.** `esp_wireguard_connect()` tính X25519 (NaCl ref) ngay trên stack của task gọi, dùng ~4.8 KB > 4 096 B được cấp. Đã tăng lên 8 192 B (`main/wg_tunnel.c`); còn dư 3 360 B.
3. (Tooling) `wg_bench.py` dừng cả lượt khi một bài lỗi và làm mất số đã đo. Giờ mỗi bài lỗi được ghi `"error"` rồi chạy tiếp.

### 6.3. Lần chạy 2 (2026-11-24, chiều): M10 peer restart, M11 power save, stack `tiT`

**Stack `tiT`.** `CONFIG_LWIP_TCPIP_TASK_STACK_SIZE` 5 120 → 6 144 B; `stack_free_min` của `tiT` còn 1 684 B suốt lần chạy M10.

**M10: peer tắt rồi bật lại.** Kịch bản (`.\tm peer-down`, chờ, `.\tm peer-up`). Đo bằng ping 1 s tới `10.66.0.2` và log serial; board không reboot (1 lần boot, 0 panic, heap min 175 924 B).

| Peer tắt | Ping mất từ → thông lại | Thông lại sau khi peer bật | Sự kiện firmware |
|---|---|---|---|
| 30 s | 12:09:10 → 12:10:51 (101 s) | **71 s** | không có `wg_peer_stale` / `wg_down` |
| 120 s | 12:11:11 → 12:13:16 (125 s) | **5 s** | không có `wg_peer_stale` / `wg_down` |

Nguyên nhân thời gian không đều: `esp_wireguardif_peer_is_up()` chỉ kiểm tra còn keypair hợp lệ (`curr_keypair.valid || prev_keypair.valid`), không xét `last_rx`. Thư viện cũng không làm "gửi dữ liệu mà không nhận gì trong KEEPALIVE + REKEY_TIMEOUT thì handshake lại" của WireGuard chuẩn. Vì vậy:
- Nhánh `peer_timeout` của `wg_sup` (restart tunnel sau `POC_WG_PEER_DOWN_RESTART_S`) **không bao giờ chạy**.
- Tunnel chỉ phục hồi khi ESP32 tự rekey theo `REKEY_AFTER_TIME` = 120 s kể từ lần handshake trước. Peer Windows không có `Endpoint` của ESP32 nên không thể tự khởi tạo handshake.
- Trường hợp xấu nhất: mất ~120 s sau khi peer đã sẵn sàng.

**M11: Wi‑Fi PS `min_modem`.** Firmware ổn định (1 boot, 0 panic). RTT tăng mạnh so với `none` (p50 155 ms so với ~19 ms lúc mạng tốt) vì radio ngủ giữa các DTIM beacon. Throughput: TCP TX 0.17 Mbit/s, UDP/TCP RX timeout. Cùng điều kiện RF xấu nên chỉ dùng được để so sánh định tính. Chưa đo dòng tiêu thụ (M13).

### 6.2. Việc còn lại

- Đo lại M8/M9 trong môi trường RF tốt (RSSI > −60 dBm, AP cố định) để tách được chi phí WireGuard.
- **Phát hiện peer mất (từ M10):** cần liveness thật dựa trên `last_rx` của peer (vá `esp_wireguard`: expose `last_rx` hoặc làm cơ chế handshake lại khi không nhận được gì sau KEEPALIVE + REKEY_TIMEOUT), để `peer_timeout` hoạt động và recovery < ~30 s.
- M10 phần mất Wi‑Fi AP 30 s / 5 phút và rút nguồn board; M11 đo lại khi RF ổn định; M12 (soak 24 h); M13 (dòng tiêu thụ).
- Cập nhật các mục **[E]** trong `docs/01-feasibility-report.md`. Số đo được tới giờ: heap rảnh có tunnel ~186 KB, block lớn nhất 110 KB, nằm trong ước lượng ~180–230 KB / ~100–110 KB.

## 7. Tiêu chí hoàn thành Phase 2

- Tunnel lên ổn định, `ping 10.66.0.2` từ peer. ✅ (2026-11-24, peer Windows; chưa có số ổn định dài hạn)
- Điền đủ bảng mục 6 cho cấu hình PS none; có ít nhất M6, M8, M9 cho PS min-modem.
- Không reboot, heap không giảm dần trong soak test 24 h.
- Cập nhật các mục **[E]** trong `docs/01-feasibility-report.md` (4.1, 4.4, 4.5) bằng số đo.

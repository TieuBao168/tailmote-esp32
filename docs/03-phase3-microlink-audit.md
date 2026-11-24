# Phase 3, bước 1: Audit MicroLink cho ESP32 DOIT DevKit V1 (không PSRAM)

> **Ngày:** 2026-11-24. **Đối tượng:** [`CamM2325/microlink`](https://github.com/CamM2325/microlink) `v2.3.0-2-g1cb8d1c` (commit `1cb8d1c`, 2026-11-18), MIT. Fork [`espkvm/microlink`](https://github.com/espkvm/microlink) dừng ở 9 commit (2026-03-17): **không dùng**. Mã nguồn tải về `.audit/` (git-ignored) chỉ để đọc và build thử, chưa đưa vào dự án.
>
> Ký hiệu giống báo cáo Phase 1: **[V]** đã kiểm chứng (đọc code / build trên máy này), **[S]** theo tài liệu hoặc commit của MicroLink, **[E]** ước lượng.

## 1. Kết luận

**Dùng MicroLink làm nền cho Phase 3: GO, kèm 3 điều kiện.**

1. **Ghim phiên bản** (`v2.3.0` + commit cụ thể) và nhúng vào `components/` như `esp_wireguard`, vì sẽ phải vá.
2. **Netmap trên ESP32 không PSRAM:** MicroLink parse MapResponse thành **cây cJSON đầy đủ** (2–3 lần kích thước JSON) trong buffer 64 KB trở lên. Đủ cho tailnet nhỏ (≈ 5–10 peer **[E]**). Lớn hơn thì cần parser streaming, đúng như khuyến nghị Phase 1.
3. **Chip ESP32 cổ điển chưa nằm trong CI bắt buộc của MicroLink** (job `esp32` là *experimental*). Mọi số liệu runtime phải tự đo trên board này.

MicroLink đã có gần như mọi khối Phase 1 yêu cầu, không phải viết lại:

| Khối (báo cáo Phase 1, mục 2.2) | MicroLink | Ghi chú |
|---|---|---|
| Control plane ts2021 (Noise IK + HTTP/2) | ✅ `ml_coord.c`, `ml_noise.c`, `ml_h2.c` **[V]** | Tailscale hosted, Headscale 0.29.7, Ionscale 0.18 **[S]** |
| Node identity (machine/node/disco key) | ✅ lưu NVS | NVS **không mã hóa** mặc định **[S]** |
| Peer discovery (netmap) | ✅ cJSON DOM | Không streaming (điều kiện 2) |
| DERP relay (TLS 443) | ✅ `ml_derp.c`, mbedTLS, verify cert mặc định **[V]** | Region DERP đặt tay (`ML_DERP_REGION`, mặc định 9 = Dallas) |
| NAT traversal (STUN + DISCO) | ✅ `ml_stun.c`, DISCO | Phase 1 định để Phase 4; có sẵn |
| PacketFilter (ACL) | ✅ `ml_filter.c`, mặc định *Enforce* **[S]** | Chưa có policy thì từ chối mọi kết nối vào |
| WireGuard data plane | ✅ `wireguard_lwip` riêng (cùng gốc smartalock với `esp_wireguard`) | Thay `esp_wireguard` của Phase 2 |

## 2. Build thử cho đúng board này [V]

Ví dụ `basic_connect`, ESP-IDF v5.3.6, build trên Windows bằng env của `tm`. Defaults = ví dụ + `sdkconfig.size.defaults` + overlay không PSRAM:

```
CONFIG_IDF_TARGET="esp32"   CONFIG_SPIRAM=n          CONFIG_BT_ENABLED=n
CONFIG_ML_H2_BUFFER_SIZE_KB=64   CONFIG_ML_JSON_BUFFER_SIZE_KB=64   CONFIG_ML_MAX_PEERS=8
CONFIG_LWIP_TCP_SND_BUF_DEFAULT=5744   CONFIG_LWIP_TCP_WND_DEFAULT=5744
```

| Thông số | MicroLink `basic_connect` | Phase 2 PoC (so sánh) |
|---|---|---|
| Kết quả | **build OK**, không lỗi | — |
| Ảnh app | 800 256 B (0xc3600) | 793 168 B |
| DRAM tĩnh | 44 628 B (24.7 %) | 47 636 B |
| IRAM | 89 906 B (68.6 %) | 95 662 B |
| `libmicrolink.a` | 93 KB (Flash 85 KB, DRAM 7.2 KB) | `esp_wireguard` 21 KB |
| mbedTLS (crypto + tls + x509) | ~111 KB Flash | — |

Ảnh 800 KB vừa phân vùng OTA 1.875 MB của dự án (mỗi slot còn trống ~57 %).

## 3. Ngân sách RAM runtime [E]

Từ số đo Phase 2: heap lúc boot 261 KB, sau Wi‑Fi ~212 KB, khối liền kề lớn nhất 110 KB.

| Khối | Ước lượng | Nguồn |
|---|---|---|
| Heap sau Wi‑Fi | ~212 KB | đo Phase 2 |
| Stack 4 task MicroLink (8 + 14 + 12 + 8) | −42 KB | README **[S]**, `microlink_internal.h` **[V]** |
| Socket UDP (4 KB/socket, 1–2 socket) | −4…8 KB | **[S]** |
| Phiên mbedTLS tới DERP | −35…40 KB | Phase 1 mục 4.1 |
| `microlink_t`, bảng peer (8 × ~200 B), NVS cache | −10…15 KB | **[E]** |
| **Còn lại trước khi lấy netmap** | **~100–120 KB** | |
| Buffer HTTP/2 (`ML_H2_BUFFER_SIZE_KB`, một khối liền) | −64 KB | Kconfig **[V]** |
| Cây cJSON (≈ 3 KB JSON/peer **[S]**, × 2–3) | −6…9 KB/peer | Kconfig **[V]** |

→ Với buffer 64 KB, phần dư sau khi parse là khoảng **35–55 KB cho 0 peer**, rồi giảm 6–9 KB mỗi peer: **khoảng 5–8 peer** trước khi hết RAM **[E]**. Khối 64 KB phải liền kề; hiện khối lớn nhất là 110 KB, nhưng sẽ nhỏ đi khi TLS và các task đã cấp phát. **Đây là rủi ro số 1 cần đo đầu tiên trên board.**

## 4. Chất lượng mã và bảo mật

| Hạng mục | Quan sát | Đánh giá |
|---|---|---|
| Crypto Noise IK | `Noise_IK_25519_ChaChaPoly_BLAKE2s`, HKDF/HMAC trên BLAKE2s, X25519 + BLAKE2s + ChaCha20-Poly1305 lấy từ `wireguard_lwip`; ephemeral key từ `esp_fill_random`; xóa DH output và khóa trung gian sau dùng **[V]** | Đúng spec. Có test KAT (`test/host/test_crypto_kat.c`, `test_noise_aead.c`) **[V]** |
| Khóa công khai control server | Khóa Tailscale hardcode; Headscale dùng `CONFIG_ML_CTRL_NOISE_PUBKEY` **[V]** | Đúng mô hình Tailscale |
| TLS | DERP verify cert theo CA bundle của IDF, mặc định bật; control plane TLS tùy chọn, verify mặc định bật **[V]** | Tốt; `sdkconfig.size.defaults` cắt CA bundle chỉ còn CA phổ biến |
| Parser (HTTP/2, Noise frame, JSON) | Có kiểm tra độ dài frame/buffer **[V]**; lịch sử có lỗi thật: *heap overflow on short Noise msg2* (sửa 2026-11-16), *integer underflow on small H2/JSON buffers* (sửa 2026-09) **[V]** | ⚠️ Mã đang được gia cố. **Chưa có fuzzing** (chỉ CodeQL + test host). Cần fuzz riêng |
| Lưu khóa | NVS không mã hóa; credential Kconfig nằm trong ảnh firmware **[S]** | Cần flash + NVS encryption khi triển khai; board rev v1.0 chỉ có Secure Boot V1 |
| Test tích hợp | CI chạy ESP32 trong **QEMU + Headscale** (`tests/conformance/headscale_qemu.sh`) mỗi lần push **[V]** | Rất đáng dùng lại cho test cuối |
| Hoạt động | 216 commit, phát hành v2.3.0, commit gần nhất 2026-11-18 | Đang được bảo trì |

Giới hạn chức năng theo README **[S]**: chỉ IPv4 trong tunnel; không dùng subnet route hay exit node của node khác; node đăng nhập tương tác không tự gia hạn node key (cần tắt key expiry); không có MagicDNS server, Taildrop, Tailscale SSH.

## 5. Kế hoạch Phase 3

| Bước | Việc | Đầu ra / tiêu chí |
|---|---|---|
| 3.1 ✅ | Nhúng MicroLink `v2.3.0@1cb8d1c` vào `firmware/phase3-tailscale/components/microlink` (commit nguyên bản trước, vá sau); app tối thiểu (`main/main.c`) + `metrics.c` của Phase 2; UDP echo cổng 7 cho `wg_bench.py rtt` | **Xong 2026-11-24:** build OK cho esp32, ảnh 990 KB (phân vùng còn 50 %) |
| 3.2 | **Chạy lần đầu trên board**: đăng ký vào tailnet (Tailscale hosted + auth key, hoặc Headscale), ping từ một máy Tailscale | `heap_delta` từng giai đoạn: Noise/register, map, DERP TLS, WireGuard; heap min, khối lớn nhất |
| 3.3 | Quyết định netmap: nếu 3.2 cho thấy dư > 40 KB với tailnet thật thì giữ cJSON + giới hạn peer; nếu không thì viết parser streaming cho MapResponse | số peer tối đa đo được |
| 3.4 | Fuzz trên host (libFuzzer/AFL, chạy trên Linux CI) cho Noise frame, HTTP/2, JSON MapResponse | 0 crash sau N giờ |
| 3.5 | **Test cuối** (chuyển từ Phase 2): soak 24 h, throughput direct và qua DERP, recovery khi mất Wi‑Fi / control / DERP / peer, PacketFilter | bảng kết quả + cập nhật mục **[E]** của báo cáo Phase 1 |

**Chạy bước 3.2** (đã chọn Tailscale hosted):

1. Tạo auth key ở https://login.tailscale.com/admin/settings/keys (Reusable + Pre-approved), dán vào `TS_AUTH_KEY` trong `secrets/secrets.env`. Bỏ trống thì board in URL đăng nhập ra cổng serial.
2. `tm config`, `tm build`, `tm flash-monitor` (firmware mặc định giờ là `firmware/phase3-tailscale`, xem `config/project.env`).
3. Kỳ vọng trên log: `ml_state` REGISTERING → CONNECTED, `ml_first_netmap`, rồi `ml_peer`. Từ một máy Tailscale: `ping <100.x.y.z>` và `python tools/bench/wg_bench.py rtt --host <100.x.y.z>`.

Lựa chọn ban đầu (để tham khảo), một control server:
- **Tailscale (hosted):** đăng nhập tailscale.com, tạo một **auth key** (nên chọn *reusable* + *tagged* / *pre-approved*) và tự dán vào file credential (git-ignored); mình không xử lý khóa thay bạn. Cần thêm ít nhất một máy đã cài Tailscale để ping thử (laptop này được).
- **Headscale (tự host):** server mã nguồn mở, cần Linux/Docker (máy này hiện không có). Phù hợp hơn cho test tự động về sau (CI QEMU của MicroLink đã dùng Headscale).

## 6. Bước 3.2: chạy lần đầu trên board (2026-11-24, Tailscale hosted)

Firmware `firmware/phase3-tailscale` (MicroLink v2.3.0, buffer netmap 64 KB, 8 peer), auth key *reusable* + `tag:esp`. Tailnet 4 máy (board + laptop Windows + điện thoại + 1 server offline).

**Kết quả: board là một node Tailscale hoạt động.**

| Mốc (từ lúc boot) | Thời gian | Ghi chú |
|---|---|---|
| Wi‑Fi có IP | 3.8 s | AP mạnh nhất, −49 dBm (xem dưới) |
| Noise IK handshake / Register | 377 ms / 335 ms | `controlplane.tailscale.com:80` (lần chạy trước) |
| Netmap đầu tiên | 1.8 s, 20 KB, 3 peer | `END_STREAM` sau 13 Noise frame |
| `CONNECTED` (DERP lên) | **13.0 s** | IP tailnet `100.89.157.120` |

Từ laptop (Tailscale for Windows, cùng LAN): `tailscale ping` **đi thẳng** `via 192.168.1.36:51820`, 15 ms; ICMP 15–32 ms; UDP echo (`wg_bench.py rtt`, 50 gói) p50 15 ms, p95 108 ms, mất 2 %.

**RAM (internal, không PSRAM):**

| Thời điểm | Heap trống | Khối lớn nhất |
|---|---|---|
| Boot | 259 KB | 128 KB |
| Sau Wi‑Fi | 205 KB | 110 KB |
| Sau `microlink_init` (−35.8 KB) | 169 KB | 110 KB |
| Sau `microlink_start`, 4 task + socket (−45.9 KB) | **123 KB** | 110 KB |
| Sau netmap + 3 peer | ~105 KB | 94 KB |
| `CONNECTED` (thêm phiên TLS tới DERP) | 87 KB | 78 KB |
| Sau socket echo (−10.9 KB) | 61 KB | 53 KB |
| **Ổn định, 4 phút** | **42–45 KB** | **17 KB** |
| **Thấp nhất (giây 12–22 sau boot)** | **16 B** | — |

- Không rò rỉ: heap đứng yên quanh 45 KB suốt ~4 phút; không reboot, không panic, không log lỗi cấp phát.
- ⚠️ **Đỉnh lúc khởi động chạm 16 B**: lúc DERP TLS, netmap, STUN/DISCO và socket echo cùng cấp phát. Chưa thấy cấp phát nào thất bại, nhưng không còn biên.
- ⚠️ **Khối lớn nhất 17 KB ở trạng thái ổn định**: một lần nhận lại netmap đầy đủ cần buffer liền 64 KB (`ML_H2_BUFFER_SIZE_KB`) sẽ không cấp phát được. Cần kiểm chứng khi control server gửi full map (đổi ACL, thêm peer).
- Stack còn dư (min free): `ml_derp_tx` 10.4 KB / 14 KB, `ml_coord` 5.1 KB / 12 KB, `ml_wg_mgr` 5.3 KB / 8 KB, `ml_net_io` 4.6 KB / 8 KB, `tiT` 4.7 KB / 6 KB → có thể thu hồi ~15–20 KB.

**Wi‑Fi mesh.** Lần chạy đầu board vào AP xa (−82 dBm): gói nhỏ qua được nhưng tải netmap chỉ nhận 1 segment rồi kẹt ~27 s và bị server reset, lặp vô hạn ở `RECONNECTING`. Sửa trong `main.c`: `scan_method = WIFI_ALL_CHANNEL_SCAN`, `sort_method = WIFI_CONNECT_AP_BY_SIGNAL` → board chọn AP gần (−49 dBm). Không cần đổi chỗ board.

### Bước tiếp theo (3.3: ngân sách RAM): xem mục 7

1. Thu nhỏ stack task theo số đo (giữ biên ≥ 2 KB) và `tiT` về 4–5 KB.
2. Tìm thứ chiếm ~45 KB sau `CONNECTED` và đỉnh 16 B lúc khởi động (heap trace theo giai đoạn; mbedTLS dynamic buffer của DERP; socket echo 10.9 KB).
3. Thử full netmap khi đang chạy (đổi ACL / thêm máy) để xem buffer 64 KB có cấp phát được không. Nếu không: buffer nhận netmap theo từng phần / parser streaming (điều kiện 2 của mục 1).

## 7. Bước 3.3: ngân sách RAM (2026-11-24)

Chẩn đoán thêm vào app: callback `heap_caps_register_failed_alloc_callback` (bỏ qua các lần MicroLink thử PSRAM rồi lùi về RAM thường: `caps` có `MALLOC_CAP_SPIRAM`), và METRIC `heap_low` mỗi khi heap thấp nhất giảm ≥ 2 KB, kèm trạng thái MicroLink.

**Nguyên nhân thật của đỉnh 16 B:** ngay sau `CONNECTED`, control server gửi lại **netmap đầy đủ 21 KB** trên long-poll. Lúc đó phiên TLS tới DERP đã chiếm RAM; cây cJSON (2–3× text) làm heap cạn, cấp phát thất bại và tin nhắn bị bỏ: `Long-poll message: JSON parse failed (21186 bytes)`. Đây là **lỗi chức năng**: mọi netmap đầy đủ về sau (thêm máy, đổi ACL) cũng sẽ mất.

Thành phần netmap (đo từ DERP map của laptop): **DERPMap ~16 KB** (28 region, 95 node) trên tổng 21 KB; MicroLink chỉ dùng region nhà.

**Thay đổi** (chi tiết: `components/microlink/TAILMOTE_PATCHES.md`):

| # | Thay đổi | Tác dụng |
|---|---|---|
| 1 | Stack task MicroLink theo số đo: net_io 8→6 KB, derp_tx 14→7 KB, coord 12→9 KB, wg_mgr 8→5 KB; `tiT` 6→4.5 KB | −16.5 KB cố định |
| 2 | mbedTLS: `DYNAMIC_FREE_CONFIG_DATA`, `DYNAMIC_FREE_CA_CERT`, không giữ cert của peer | giảm RAM của phiên DERP |
| 3 | `ml_json_prune()`: xóa (thay bằng dấu cách) 13 khóa MicroLink không đọc, ở mọi cấp | −2.2 KB text / netmap |
| 4 | `ml_json_prune_derp_regions()`: chỉ giữ region DERP nhà (không có region nhà thì không cắt) | −15.5 KB text / netmap |

**Kết quả** (cùng tailnet 4 máy):

| | Trước 3.3 | Sau 3.3 |
|---|---|---|
| Heap sau `microlink_start` | 123 KB | **140 KB** |
| Netmap đưa vào cJSON | 21.2 KB | **3.5 KB** (−84 %) |
| Netmap long-poll sau `CONNECTED` | ❌ parse failed | ✅ |
| Cấp phát thất bại | 1 (heap còn 16 B) | **0** |
| Heap thấp nhất | 16–52 B | **36.4 KB** |
| Heap ổn định / khối lớn nhất | 45 KB / 17 KB | **69 KB / 26.6 KB** |
| Stack còn trống thấp nhất | — | `ml_coord` 2.0 KB, `ml_wg_mgr` 2.2 KB, `ml_net_io` 2.6 KB, `ml_derp_tx` 3.1 KB |
| CONNECTED sau boot | 13.0 s | 11.5 s |

Kết nối sau thay đổi: `tailscale ping` vẫn đi thẳng; UDP echo 64 B mất 2 %, 1200 B mất 0 %; RTT p50 27–47 ms (dao động theo Wi‑Fi trong lần đo này).

**Sức chứa còn lại [E]:** sau cắt tỉa mỗi peer còn khoảng 1–1.5 KB text (≈ 3–4 KB trong cây cJSON). Heap thấp nhất 36 KB và khối lớn nhất 26 KB cho phép thêm khoảng **8 peer** (= `ML_MAX_PEERS`). Tailnet lớn hơn cần parser streaming.

**Còn lại cho Phase 3:** đo lại sức chứa với tailnet thật > 4 máy; fuzz `ml_json_prune` cùng parser H2/Noise (bước 3.4); test cuối (3.5).

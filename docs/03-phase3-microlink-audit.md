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

## 8. Bước 3.4: fuzz (2026-11-24)

Máy build là Windows, chỉ có MinGW gcc (không có clang / libFuzzer / ASan). Cách làm:

- **Target theo chuẩn libFuzzer** (`firmware/phase3-tailscale/test/fuzz/fuzz_json_prune.c`): chạy nguyên trạng với clang `-fsanitize=fuzzer,address,undefined` trên CI Linux sau này.
- **Driver tự viết** (`driver.c`): sinh JSON có cấu trúc giống MapResponse (DERPMap/Regions với ID dạng số, khóa bị cắt ở mọi cấp, khóa trùng, khoảng trắng lạ) + đột biến (lật bit, chèn token, xóa/nhân đoạn, cắt cụt, ghép). Mỗi buffer đặt sát **guard page** (luân phiên đầu/cuối), đọc vượt 1 byte là crash.
- **Oracle ngữ nghĩa** với cJSON của ESP-IDF build trên host: chỉ đổi byte thành dấu cách; idempotent; JSON hợp lệ vẫn hợp lệ; cây sau cắt = cây gốc trừ các khóa bị cắt và region không giữ.
- Chạy: `tm fuzz --seconds N`; `tm test` chạy 3 giây.

**Fuzzer tìm ra 5 lỗi trong bộ cắt JSON của bước 3.3** (chi tiết: `components/microlink/TAILMOTE_PATCHES.md`, mục 3), trong đó có **1 lỗi đọc vượt buffer** (netmap bị cắt cụt ngay sau `"DERPMap":`) và 4 trường hợp JSON hỏng khiến lượt cắt thứ hai cắt thêm. Sửa tận gốc: chỉ cắt khi cả tài liệu là JSON hợp lệ (kiểm tra cú pháp chặt, bộ nhớ cố định), cắt region trước, cắt khóa sau.

**Sau bản sửa cuối:** khoảng **9,8 triệu input, 0 lỗi** (5 seed × 60 s + 1 lượt 600 s). Trên board: netmap thật vẫn qua kiểm tra cú pháp và vẫn được cắt 17,7 / 21,2 KB; 0 cấp phát thất bại; heap thấp nhất 36 KB.

**Chưa fuzz được:** parser khung Noise và HTTP/2 chiều nhận của MicroLink. Chúng là hàm `static` trong `ml_coord.c`, đọc thẳng từ socket. Cần một harness giả lập socket (stub `recv`) hoặc tách hàm, chạy trên CI Linux với ASan + libFuzzer, giống bộ test host có sẵn của MicroLink (`test/host`, dùng `-fsanitize=address,undefined`).

## 9. Bước 3.5: test cuối (2026-11-24)

Firmware `firmware/phase3-tailscale` (MicroLink v2.3.0 + bản vá mục 7–8), ESP32-D0WD rev 1.0 không PSRAM, Wi‑Fi mesh (AP gần nhất, RSSI −49…−52 dBm), Tailscale hosted, tailnet 4 máy. Máy đo: laptop Windows cùng LAN. Công cụ: `wg_bench.py` (RTT, UDP sink dồn chung cổng echo 7), `tools/bench/ts_recovery.py`, log serial `METRIC`. Số thô: `results/phase3/` (git-ignored).

**Thay đổi firmware cho test:** cổng 7 vừa echo vừa làm UDP sink (`RST`/`STAT`, gói `SINK…`), vì mỗi socket MicroLink tốn ~11 KB RAM; test hook `WIFI_DOWN <s>` (`CONFIG_TM_TEST_HOOKS`, **tắt khi dùng thật**); tùy chọn `TS_DERP_REGION` trong form secrets.

### 9.1. Hiệu năng, đường trực tiếp

| Đo | Kết quả |
|---|---|
| RTT 64 B / 1200 B (200 gói) | p50 22 / 33 ms, p95 100 / 115 ms, mất 0,5 % |
| UDP PC → ESP32, 1200 B | 0,5 / 1 / 2 Mbit/s: **mất 0 %**; 4 Mbit/s: 3,6 Mbit/s, mất 5 %; 8 Mbit/s: 4,1 Mbit/s (trần) |
| TCP | chưa đo (firmware chưa có dịch vụ TCP qua API của MicroLink) |

Trần ~4 Mbit/s nằm trong ước lượng Phase 1 (1–4 Mbit/s cho đường trực tiếp).

### 9.2. DERP

- Region mặc định của MicroLink là 9 (Dallas). Từ Việt Nam: `tailscale netcheck` cho Singapore (3) 27 ms, Dallas không nằm trong top 8 (> 176 ms). Đổi `TS_DERP_REGION=3`: kết nối DERP 4,2 s → **2,6 s** (TCP 254 → 34 ms), `CONNECTED` sau boot 11,5–13 s → **9,8 s**. Bộ cắt netmap giữ đúng region 3.
- **Chưa đo được throughput/RTT qua relay từ laptop này:** chặn đường trực tiếp bằng Windows Firewall không có tác dụng (Tailscale for Windows cài bộ lọc WFP riêng, ưu tiên cao hơn), và các trường `enable_stun` / `enable_disco` của MicroLink bị bỏ qua. Cần đo từ một máy không đi thẳng được tới board (điện thoại dùng 4G).

### 9.3. Phục hồi (probe UDP qua tailnet mỗi 0,5 s)

| Kịch bản | Mất liên lạc | Thông lại sau khi hết nguyên nhân |
|---|---|---|
| Board mất Wi‑Fi 30 s | 36,8 s | **6,7 s** |
| Board mất Wi‑Fi 120 s | 125,2 s | **4,8 s** |
| Laptop `tailscale down` 30 s rồi `up` | 56,4 s | **5,4 s** |
| Board reboot (≥ 7 lần trong các lượt chạy) | — | `CONNECTED` sau 9,8 s (DERP Singapore), 11,5–13 s (Dallas) |

Đường dữ liệu thông lại trước khi control plane `CONNECTED` lại (~20 s sau khi có Wi‑Fi), vì phiên WireGuard còn hiệu lực. Trong lúc kết nối lại, heap thấp nhất 26,6 KB; không cấp phát thất bại, không lỗi parse.

### 9.4. Soak 30 phút (theo lựa chọn rút gọn, 22:48–23:23)

| | Kết quả |
|---|---|
| Reboot / panic / cấp phát thất bại / lỗi parse / kết nối lại | **0 / 0 / 0 / 0 / 0** |
| Heap (sau 1 phút) | min / tb / max 57,1 / 67,5 / 68,9 KB; tb 10 mẫu đầu 68,3 KB → 10 mẫu cuối 68,2 KB (**không rò rỉ**) |
| Heap thấp nhất / khối lớn nhất (min) | 40,5 KB / 28,7 KB |
| CPU trung bình | core 0: 2,9 %, core 1: 1,2 % |
| Stack còn trống thấp nhất | `ml_coord` 2,0 KB, `ml_wg_mgr` 2,2 KB, `ml_net_io` 2,6 KB, `tiT` 3,0 KB, `ml_derp_tx` 3,1 KB |
| UDP echo 1 Hz (1800 gói) | p50 20 ms, p95 476 ms, max 1,0 s, **mất 6 %** |

⚠️ Mất 6 % cao hơn các bài ngắn (0–2 %). Chưa phân biệt được do Wi‑Fi hay do chu kỳ rekey WireGuard (120 s): cần ghi thời điểm từng gói mất để đối chiếu.

### 9.5. Đánh giá tiêu chí (chuyển từ Phase 2, mục 7)

| Tiêu chí | Trạng thái |
|---|---|
| Board là node Tailscale, ping/UDP qua tailnet | ✅ |
| RAM / Flash / CPU / stack trên firmware cuối | ✅ mục 7, 9.4 |
| Throughput / latency đường trực tiếp | ✅ 9.1 (UDP); TCP chưa đo |
| Throughput / latency qua DERP | ❌ chưa đo được từ máy này (9.2) |
| Phục hồi khi mất Wi‑Fi / peer / reboot | ✅ 9.3 (Wi‑Fi mô phỏng từ phía board; chưa tắt AP thật, chưa mất control server riêng) |
| Soak không reboot, heap không giảm dần | ✅ 30 phút (tiêu chí gốc 24 h: rút gọn theo yêu cầu) |
| ACL / PacketFilter | ⚠️ chỉ policy mặc định (cho phép tất cả); chưa thử policy chặn |

### 9.6. Việc còn lại

1. Điều tra 6 % mất gói trong soak (log thời điểm mất, đối chiếu rekey / Wi‑Fi).
2. Đo qua DERP từ một máy ngoài LAN (điện thoại 4G).
3. Thử ACL chặn (ví dụ chỉ cho phép UDP 7) để kiểm tra PacketFilter trên thiết bị.
4. Fuzz parser Noise / HTTP/2 chiều nhận (cần CI Linux + ASan, mục 8).
5. Tắt `CONFIG_TM_TEST_HOOKS` và bật flash/NVS encryption cho bản dùng thật.

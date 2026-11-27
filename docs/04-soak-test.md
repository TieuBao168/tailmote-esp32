# Soak test: board chạy dài ngày (exit node, bản `--prod`)

Mục tiêu: xác nhận ESP32 chạy firmware `firmware/phase3-tailscale` bản `--prod` có bật exit node có giữ ổn định khi chạy liên tục nhiều ngày không. Cụ thể: không reboot, không rò RAM, tự phục hồi khi mất mạng, và stack không cạn.

## Công cụ

`tools/bench/soak.py` có hai lệnh:

| Lệnh | Việc làm |
|---|---|
| `tm soak` (`make soak`) | Mở cổng serial ở chế độ **độc quyền**, rồi ghi mọi dòng kèm ngày giờ đầy đủ vào `results/soak/soak-YYYYMMDD.log`, mỗi ngày một file. Khi rút cáp USB hoặc gặp lỗi đọc, nó tự mở lại cổng. Trên board này, mở cổng sẽ **reset chip**, nên mỗi lần mở được ghi dấu `# capture: opened …`. Lần boot ngay sau dấu đó không bị tính là crash |
| `tm soak-report` (`make soak-report`) | Đọc toàn bộ log soak, chấm từng case **PASS / WARN / FAIL**, rồi ghi `results/soak/report.md`. Lệnh này chạy được bất cứ lúc nào trong khi đang ghi log |

Chạy nền dài ngày (vẫn chạy khi đóng terminal, và tự khởi động lại nếu bị lỗi):

```bash
systemd-run --user --unit=tailmote-soak --property=Restart=on-failure --property=RestartSec=15 \
  --working-directory="$PWD" ~/.espressif/python_env/idf5.3_py3.12_env/bin/python -u \
  tools/bench/soak.py capture --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --dir results/soak
```

```bash
systemctl --user status tailmote-soak
```

```bash
systemctl --user stop tailmote-soak
```

Dịch vụ user sẽ dừng khi bạn đăng xuất, trừ khi bật lingering bằng `loginctl enable-linger $USER`. Trong lúc soak, **đừng mở cổng ESP32 bằng Tabby hay `tm monitor`**. Cổng đang bị giữ độc quyền nên các công cụ đó sẽ báo lỗi. Muốn xem log trực tiếp thì dùng `tail -f results/soak/soak-*.log`.

## Các case và ngưỡng

Ngưỡng mặc định có thể đổi bằng tham số của `soak.py report` (ví dụ `--heap-min`, `--max-outage-s`).

| Case | Nội dung | PASS khi |
|---|---|---|
| **S1** | Không reboot ngoài ý muốn, không crash | Mọi sự kiện `boot` đều ngay sau một lần mở cổng, và không có `Guru Meditation`, `abort()`, `Backtrace`, brownout hay watchdog |
| **S2** | Heap | Không có `alloc_failed`. `heap_min` ≥ 10 KB. Xu hướng heap trống (hồi quy tuyến tính, sau 5 phút khởi động, cần ≥ 30 phút dữ liệu) không giảm quá 512 B/giờ |
| **S3** | Tailnet | Thời gian ở trạng thái `CONNECTED` ≥ 99 %, tính theo đồng hồ của máy ghi log, kể cả lúc board reboot. Không lần mất kết nối nào dài quá 120 s |
| **S4** | Wi-Fi | Sau mỗi `wifi_disconnected`, board có lại `wifi_got_ip` trong vòng 120 s |
| **S5** | Stack | Mọi task còn ≥ 256 B stack trống (`stack_free_min`) |
| **S6** | CPU | Trung vị tải core 0 ≤ 10 % (khi gần như rảnh) |
| **S7** | Traffic qua board | Bộ lọc gói đã nhận policy. Report ghi số gói được cho qua và bị chặn (từ điện thoại khi dùng exit node) |

Report còn có bảng các lần **mất tailnet** (thời điểm, thời lượng), và các dòng **`W`/`E` gom theo nội dung**, ví dụ `ml_derp: TLS write timeout`. Hai bảng này giúp nhìn ra lỗi lặp lại.

## Kịch bản đề xuất

1. **Rảnh 24 giờ** (S1–S6): board là exit node nhưng không ai dùng.
2. **Có tải** (S2, S7): vài lần trong ngày, dùng điện thoại với exit node ESP32 trong 10–15 phút (đọc web, mạng xã hội). Sau mỗi lần, `heap_min` phải vẫn ≥ 10 KB, và không có `alloc_failed`.
3. **Sự cố mạng tự nhiên** (S3, S4): không gây ra chủ động, chỉ ghi lại và đánh giá thời gian phục hồi. Nếu muốn thử chủ động, rút nguồn router hoặc AP khoảng 1 phút.
4. **72 giờ trở lên**: chạy lại `tm soak-report` mỗi ngày, so sánh xu hướng heap (S2) giữa các ngày.

## Kết quả

Ghi lại kết quả từng đợt soak ở đây: thời gian, phiên bản firmware (`app` trong sự kiện `boot`), và bảng case trích từ `results/soak/report.md`.

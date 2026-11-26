# AccountHubIEC Monitor — Server Web Application

Ứng dụng Web hoàn chỉnh phía server dùng để tiếp nhận dữ liệu giám sát từ `client_bot.py`, lưu trữ lịch sử bằng SQLite (WAL), đẩy cập nhật thời gian thực qua **Server-Sent Events (SSE)**, theo dõi vòng đời sự cố cảnh báo, gửi thông báo Webhook và trực quan hóa đa trang (không cần cài thư viện bên ngoài).

## Kiến trúc dự án

```text
AccountHubIEC/
├── client_bot.py                  # Agent chạy trên máy chủ cần giám sát
├── client_bot_report.json         # Mẫu báo cáo JSON từ client_bot.py
├── server.py                      # Entrypoint khởi chạy Web App Server
├── server_app/                    # Backend Python (modular, zero external deps)
│   ├── config.py                  # Cấu hình CLI & biến môi trường
│   ├── store.py                   # SQLite WAL: hosts, reports, alert_events, alert_acks, settings, CSV export
│   ├── sse.py                     # EventBroker đẩy dữ liệu real-time (/api/stream)
│   ├── auth.py                    # Bearer token cho client_bot + Cookie session tùy chọn cho Web UI
│   ├── webhooks.py                # Gửi cảnh báo tự động tới Discord / Slack / HTTP Webhook
│   └── http_server.py             # ThreadingHTTPServer REST API + SSE + Static SPA
├── static/                        # Frontend Multi-View SPA (ES Modules, Dark/Light Theme)
│   ├── index.html
│   ├── css/ (tokens.css, app.css)
│   └── js/
│       ├── main.js, api.js, format.js, charts.js
│       └── views/ (fleet.js, host.js, accounts.js, users.js, alerts.js, settings.js)
└── deploy/
    └── accounthub-monitor.service # File systemd service mẫu cho Linux
```

## Các trang chức năng trong Web App

1. **Toàn hệ thống (`#/fleet`)**: Tổng hợp CPU, RAM, GPU/VRAM, phiên online và cảnh báo trên toàn bộ cụm máy chủ; thẻ trạng thái từng máy có mini sparkline CPU.
2. **Chi tiết Máy chủ (`#/host/<host>`)**: Đồng hồ đo SVG (CPU, RAM, GPU VRAM), biểu đồ Canvas theo thời gian (`1h / 6h / 24h / 7d`) có tooltip tương tác, chi tiết GPU & lỗi Xid, phân vùng ổ đĩa kèm mốc 90%, **Time-Travel Snapshot** để xem lại trạng thái bất kỳ thời điểm nào trong quá khứ, ghi chú máy chủ và Raw JSON.
3. **Tài nguyên & Tiến trình (`#/accounts`)**: Xếp hạng Top Consumers (RAM / GPU VRAM / CPU), bảng lọc & sắp xếp `usage_by_account`, mở rộng từng user để xem `top_procs` (nổi bật PID `GPU-IDLE`, copy nhanh lệnh `ps -fp`), biểu đồ lịch sử sử dụng RAM/VRAM của riêng user đó và xuất CSV.
4. **Tài khoản `/home` & Hub (`#/users`)**: Biểu đồ Donut phân bố số ngày không đăng nhập, bảng kiểm toán tài khoản `/home` (lọc theo nhóm hoạt động, xuất CSV), danh sách phiên `who` đang mở và trạng thái JupyterHub.
5. **Trung tâm Cảnh báo (`#/alerts`)**: Quản lý cảnh báo đang hoạt động (xác nhận **Ack/Mute** kèm ghi chú xử lý) và tab **Lịch sử sự cố (Incident Timeline)** ghi nhận thời điểm bắt đầu, số lần lặp và thời điểm hết lỗi.
6. **Cài đặt & Triển khai (`#/settings`)**: Công cụ sinh nhanh lệnh chạy `client_bot.py` & `systemd`, kéo-thả Import JSON, cấu hình & gửi thử Webhook (Discord/Slack/HTTP), tùy chỉnh ngưỡng `stale` / số ngày lưu lịch sử và quản lý xóa host.

## Hướng dẫn khởi chạy (cấu hình qua file JSON, không lộ secret trên argv)

### 1. Tạo chứng chỉ TLS nội bộ & cấu hình Server

```bash
# Tạo Private CA + chứng chỉ Server có SAN IP LAN + chứng chỉ Client mTLS
python3 deploy/gen_certs.py

# Tạo/chỉnh sửa file cấu hình server (đặt quyền chỉ chủ sở hữu đọc)
cp server_config.example.json server_config.json
chmod 600 server_config.json
```

Chạy server (tự động đọc `server_config.json`, hoặc chỉ định `--config <path>`):

```bash
python3 server.py
```

### 2. Cấu hình & chạy Client (`client/config.json`)

Copy `certs/ca.crt` sang máy client (thêm `certs/client.crt`, `certs/client.key` nếu bật mTLS), tạo file `client/config.json` từ `client/config.example.json` (`chmod 600 client/config.json`), điền cùng `security.token` rồi chạy:

```bash
sudo python3 client/run.py
```

> Khi có `security.token` và `security.use_hmac: true`, `client/run.py` mặc định ký toàn bộ payload bằng **HMAC-SHA256** kèm `X-Timestamp` + `X-Nonce` chống replay và **không gửi raw token qua header cũng như không lộ trên `ps aux`**.

## Danh sách API chính

| Method | Endpoint | Mô tả |
| --- | --- | --- |
| `POST` | `/api/report` | Nhận báo cáo JSON từ `client_bot.py` (xác thực Bearer token nếu bật) |
| `GET` | `/api/stream` | Luồng SSE đẩy cập nhật thời gian thực tới Web App |
| `GET` | `/api/fleet` | Tổng hợp toàn cụm + sparkline cho từng host |
| `GET` | `/api/hosts/<host>/latest?snapshot_id=` | Báo cáo mới nhất hoặc snapshot lịch sử theo ID |
| `GET` | `/api/hosts/<host>/snapshots` | Danh sách các snapshot đã lưu cho Time-Travel |
| `GET` | `/api/hosts/<host>/history?hours=24` | Chuỗi thời gian CPU / RAM / GPU / Temp |
| `GET` | `/api/hosts/<host>/accounts/<user>/history` | Chuỗi thời gian tài nguyên theo từng user |
| `GET` | `/api/hosts/<host>/export/(accounts\|users)` | Xuất file CSV UTF-8 |
| `GET` | `/api/alerts` / `/api/alerts/events` | Cảnh báo đang mở & lịch sử sự cố |
| `POST` | `/api/alerts/ack` | Xác nhận (Ack/Unack) cảnh báo kèm ghi chú |
| `GET/POST` | `/api/settings` | Đọc/cập nhật cấu hình Webhook & lưu trữ |
| `POST` | `/api/settings/webhook-test` | Gửi thử tin nhắn kiểm tra Webhook |
| `POST` | `/api/auth/login` / `/api/auth/logout` | Đăng nhập/đăng xuất Web UI (khi bật `--admin-password`) |

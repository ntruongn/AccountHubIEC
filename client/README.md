# AccountHubIEC Multi-Task Client Agent

Agent giám sát tài nguyên chạy trên từng máy chủ dùng chung (Linux / GPU / JupyterHub / TLJH), gửi báo cáo bảo mật về **AccountHubIEC Monitor Server**.

- **Thu thập đa tác vụ (Multi-Task 3 tầng)**: các tác vụ đọc ổ đĩa nặng (`du`) chạy ngầm ở luồng riêng với độ ưu tiên thấp (`nice`/`ionice`) nên **không bao giờ làm chậm hay treo** vòng lặp gửi báo cáo nhanh (~1.1 giây/chu kỳ).
- **Bảo mật đường truyền**: HTTPS (TLS 1.2+ xác thực `ca.crt`), ký toàn vẹn **HMAC-SHA256 + chống Replay** (`X-Timestamp`, `X-Nonce`, `X-Signature`), hỗ trợ **mTLS** và đọc toàn bộ bí mật từ `client/config.json` (`chmod 600`, không lộ qua `argv` / `ps aux`).
- **Tôn trọng quyền riêng tư**: **KHÔNG** đọc nội dung file, **KHÔNG** ghi lại lệnh người dùng gõ, **KHÔNG** thu thập đối số dòng lệnh (`cmdline`) của tiến trình.

---

## 1. Kiến trúc 3 tầng thu thập song song

| Tầng | Module (`client/collectors/`) | Chu kỳ mặc định | Cơ chế thực thi |
| --- | --- | --- | --- |
| **Fast** | `system.py`, `gpu.py`, `processes.py`, `users.py` (`lastlog` 1 lần), `sessions.py` (`who`, JupyterHub API) | `60s` (`schedule.interval`) | Chạy song song qua `ThreadPoolExecutor` (`schedule.workers = 6`), hoàn tất toàn bộ trong **~1.1 giây** |
| **Medium** | `gpu_xid_errors` (`dmesg`/`journalctl`), `failed_logins` (`lastb`) | `300s` (`schedule.slow_meta_every`) | Cache theo TTL, chạy song song khi hết hạn |
| **Slow (Background)** | `disk_usage.py` (`du -sm --one-file-system /home/<user>`) | `3600s` (`schedule.disk_every`) | Luồng nền riêng với `nice -n 19 ionice -c 3` + `schedule.disk_workers = 3`, cập nhật dần từng user vào cache, không block báo cáo nhanh |

## 2. Cấu trúc thư mục

```text
client/
├── run.py                  # Script khởi chạy trực tiếp từ mọi thư mục
├── main.py / __main__.py   # Entrypoint & vòng lặp gửi báo cáo
├── config.py               # Bộ nạp cấu hình JSON & kiểm tra quyền file (0600)
├── config.example.json     # File cấu hình mẫu
├── config.json             # File cấu hình thật (chmod 600, không commit Git)
├── scheduler.py            # Bộ điều phối multi-task 3 tầng
├── alerts.py               # Luật sinh cảnh báo tiếng Việt có dấu
├── sender.py               # Gửi HTTPS/TLS + mTLS + ký HMAC-SHA256 kèm retry/backoff
├── utils.py                # Chạy subprocess an toàn kèm nice/ionice
├── requirements.txt        # Phụ thuộc Python (chỉ cần psutil)
└── collectors/
    ├── system.py           # CPU, RAM, swap, load avg, uptime, disk partitions, jupyterhub service
    ├── gpu.py              # nvidia-smi (GPU + VRAM theo PID) & lỗi Xid trong dmesg
    ├── processes.py        # Tài nguyên theo account, GPU-IDLE, zombie, top_procs, heavy
    ├── sessions.py         # Phiên đang mở (who), failed logins (lastb), JupyterHub API
    ├── users.py            # Danh sách /home & parse lastlog 1 lần duy nhất
    └── disk_usage.py       # Worker quét dung lượng /home/<user> chạy ngầm
```

---

## 3. Cài đặt trên máy chủ Client

### Bước 1: Copy mã nguồn & chứng chỉ từ Server
Copy thư mục `client/` và file chứng chỉ CA `certs/ca.crt` từ máy server sang máy client (ví dụ đặt tại `/opt/accounthub/`):

```text
/opt/accounthub/
├── certs/
│   └── ca.crt              # Bắt buộc nếu dùng HTTPS tự ký (thêm client.crt + client.key nếu bật mTLS)
└── client/
    ├── run.py
    ├── config.example.json
    └── ...
```

### Bước 2: Cài thư viện phụ thuộc

```bash
cd /opt/accounthub
pip install -r client/requirements.txt
# Hoặc trên Ubuntu 24.04 (dùng gói hệ thống nếu không tạo venv):
# sudo apt install -y python3-psutil
```

### Bước 3: Tạo file cấu hình `client/config.json`

```bash
cp client/config.example.json client/config.json
chmod 600 client/config.json
```

---

## 4. Cấu hình JupyterHub Read-Only Service (Cách 2 — Khuyến nghị)

Để client theo dõi được danh sách notebook server đang chạy (`Running/Stopped`) và thời gian bỏ treo (`[HUB-IDLE]`), client gọi `GET <hub_url>/hub/api/users`. Thay vì dùng token của tài khoản Admin toàn quyền, hãy tạo một **Service chỉ có quyền đọc (RBAC read-only)** trên máy chạy JupyterHub/TLJH.

### Bước 4.1: Tạo API token ngẫu nhiên (64 ký tự hex)

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Lưu lại chuỗi token vừa sinh (gọi là `<HUB_READONLY_TOKEN>`).

### Bước 4.2: Khai báo Service & Role chỉ đọc cho JupyterHub

#### Trường hợp A: Máy chủ dùng **TLJH (The Littlest JupyterHub)**
Tạo file `/opt/tljh/config/jupyterhub_config.d/accounthub_monitor.py`:

```bash
sudo tee /opt/tljh/config/jupyterhub_config.d/accounthub_monitor.py > /dev/null <<'EOF'
# Service chỉ đọc dành riêng cho AccountHubIEC Monitor Client
c.JupyterHub.services = list(getattr(c.JupyterHub, "services", [])) + [
    {
        "name": "accounthub-monitor",
        "api_token": "THAY_BANG_HUB_READONLY_TOKEN",
    }
]

c.JupyterHub.load_roles = list(getattr(c.JupyterHub, "load_roles", [])) + [
    {
        "name": "accounthub-monitor-readonly",
        "description": "Chi doc danh sach user, hoat dong gan nhat va trang thai server",
        "scopes": [
            "list:users",
            "read:users:activity",
            "read:servers",
        ],
        "services": ["accounthub-monitor"],
    }
]
EOF

sudo chmod 600 /opt/tljh/config/jupyterhub_config.d/accounthub_monitor.py
sudo tljh-config reload hub
```

> **Lưu ý về bảo mật:** 3 scope `list:users`, `read:users:activity`, `read:servers` chỉ cho phép **đọc metadata**. Token này **không thể** dừng/khởi động server, **không thể** tạo/xóa user và **không thể** truy cập nội dung notebook.

#### Trường hợp B: Máy chủ dùng **JupyterHub chuẩn** (không qua TLJH)
Thêm cùng đoạn cấu hình `c.JupyterHub.services` và `c.JupyterHub.load_roles` ở trên vào cuối file cấu hình của bạn (thường là `/etc/jupyterhub/jupyterhub_config.py`), đảm bảo `chmod 600` và khởi động lại dịch vụ:

```bash
sudo systemctl restart jupyterhub
```

### Bước 4.3: Kiểm tra cổng Hub nội bộ & xác minh token hoạt động

Kiểm tra JupyterHub đang lắng nghe ở cổng nội bộ nào (trên TLJH thường là `http://127.0.0.1:8000` qua proxy hoặc `http://127.0.0.1:15001` trực tiếp tới hub):

```bash
# Kiểm tra nhanh endpoint bằng curl
curl -sS -H "Authorization: token THAY_BANG_HUB_READONLY_TOKEN" \
  http://127.0.0.1:8000/hub/api/users | head -c 400
```

Nếu trả về mảng JSON danh sách user (ví dụ `[{"name": "...", "last_activity": "...", "servers": ...}]`) là đã thành công!
*(Nếu gặp `Connection refused` trên cổng `8000`, hãy thử kiểm tra bằng `sudo ss -tlnp | grep -E 'traefik|jupyterhub'` để lấy đúng cổng nội bộ — ví dụ `http://127.0.0.1:15001` hoặc cổng HTTPS của proxy.)*

---

## 5. Hoàn thiện `client/config.json`

Mở `client/config.json` và điền các thông số tương ứng:

```json
{
  "server": "https://192.168.50.181:8443/api/report",
  "output_file": "client_bot_report.json",
  "tls": {
    "ca_cert": "../certs/ca.crt",
    "client_cert": "",
    "client_key": "",
    "insecure": false
  },
  "security": {
    "token": "KHOP_VOI_SECURITY_TOKEN_CUA_SERVER",
    "use_hmac": true
  },
  "schedule": {
    "interval": 60,
    "workers": 6,
    "disk_workers": 3,
    "disk_every": 3600,
    "disk_timeout": 300,
    "disk_once_wait": 15,
    "slow_meta_every": 300,
    "cpu_sample_sec": 1.0,
    "home_dir": "/home"
  },
  "thresholds": {
    "cpu_pct": 400.0,
    "mem_gb": 32.0,
    "gpu_mb": 8000,
    "gpu_idle_minutes": 60,
    "zombies": 5,
    "disk_gb": 200.0,
    "inactive_days": 90
  },
  "jupyterhub": {
    "url": "http://127.0.0.1:8000",
    "token": "THAY_BANG_HUB_READONLY_TOKEN",
    "idle_hours": 24.0
  },
  "http": {
    "timeout": 15,
    "retries": 3
  }
}
```

### Giải thích các nhóm cấu hình chính
- **`server`**: URL API nhận báo cáo của server trung tâm (luôn ưu tiên `https://...`).
- **`tls`**:
  - `ca_cert`: đường dẫn tới `ca.crt` (đường dẫn tương đối được tính theo thư mục chứa `config.json`).
  - `client_cert` & `client_key`: chỉ điền khi server bật bắt buộc **mTLS** (`tls.require_client_cert: true`).
  - `insecure`: luôn giữ `false` trong production.
- **`security`**:
  - `token`: chuỗi bí mật dùng chung với `security.token` bên `server_config.json`.
  - `use_hmac: true`: ký toàn bộ body bằng **HMAC-SHA256** kèm `X-Timestamp` + `X-Nonce` (không truyền raw token qua header).
- **`schedule`**:
  - `interval`: chu kỳ gửi báo cáo nhanh (giây).
  - `disk_every`: chu kỳ luồng nền chạy `du` quét `/home/<user>` (mặc định `3600` = 1 giờ/lần).
  - `disk_workers`: số thư mục `/home/<user>` quét song song cùng lúc (mặc định `3` để tránh quá tải I/O).
- **`jupyterhub`**:
  - `url`, `token`: trỏ tới Hub nội bộ cùng token Read-Only vừa tạo ở Mục 4.
  - `idle_hours`: số giờ notebook chạy không có hoạt động trước khi sinh cảnh báo `[HUB-IDLE]`.

---

## 6. Chạy thử & Cài đặt dịch vụ `systemd`

### 6.1. Chạy thử 1 lần tại chỗ (`--once`)

```bash
sudo python3 /opt/accounthub/client/run.py --once
```

Kiểm tra nhanh trong JSON đầu ra:
- `collector_meta.timings_ms`: xem thời gian chạy của từng collector.
- `jupyterhub`: phải hiện danh sách user kèm `server_running` và `idle_hours` (không có `{"error": ...}`).
- `users[].disk_mb`: hiện dung lượng theo MB (nhờ chạy với `sudo`).

### 6.2. Chạy trực tiếp để kiểm tra kết nối tới Server

```bash
sudo python3 /opt/accounthub/client/run.py
```

Kết quả thành công sẽ in log dạng:
```text
[09:35:01] AccountHubIEC Client started | config=/opt/accounthub/client/config.json | server=https://192.168.50.181:8443/api/report (transport=HTTPS, auth=HMAC-SHA256, fast=60s, disk_bg=3600s)
[09:35:02] sent HTTP 200 | collect=1065.4ms | disk=scanning(4/23) | alerts=2
```

### 6.3. Cài đặt chạy nền tự khởi động với `systemd`

```bash
sudo tee /etc/systemd/system/accounthub-client.service > /dev/null <<'EOF'
[Unit]
Description=AccountHubIEC Multi-Task Resource Monitor Client
After=network-online.target jupyterhub.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/accounthub
ExecStart=/usr/bin/python3 /opt/accounthub/client/run.py --config /opt/accounthub/client/config.json
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now accounthub-client
sudo systemctl status accounthub-client
```

Xem log realtime:
```bash
sudo journalctl -u accounthub-client -f
```

---

## 7. Danh sách các cảnh báo client tự động tổng hợp

| Tag | Ý nghĩa | Ngưỡng mặc định |
| --- | --- | --- |
| `[HEAVY]` | Tài khoản tiêu thụ CPU / RAM / VRAM vượt ngưỡng | CPU ≥ 400%, RAM ≥ 32GB, VRAM ≥ 8000MB |
| `[GPU-IDLE]` | Tiến trình giữ VRAM nhưng CPU ~0% quá lâu (nghi bỏ quên kernel) | `gpu_idle_minutes: 60` |
| `[ZOMBIE]` | Tài khoản có nhiều tiến trình zombie | ≥ 5 tiến trình |
| `[DISK]` | Thư mục `/home/<user>` vượt dung lượng | ≥ 200GB |
| `[INACTIVE]` | User lâu không đăng nhập nhưng vẫn chiếm > 1GB | ≥ 90 ngày |
| `[DISK-FULL]` | Phân vùng hệ thống gần đầy | ≥ 90% |
| `[SERVICE]` | Dịch vụ `jupyterhub` không ở trạng thái `active` | != `active` |
| `[GPU-TEMP]` | Nhiệt độ GPU vượt ngưỡng an toàn | ≥ 85°C |
| `[GPU-XID]` | Xuất hiện lỗi `NVRM: Xid` trong `dmesg` / `journalctl` | Có lỗi mới |
| `[HUB-IDLE]` | JupyterHub server đang chạy nhưng không tương tác lâu | ≥ 24 giờ |

---

## 8. Xử lý sự cố thường gặp

| Hiện tượng | Nguyên nhân & Cách khắc phục |
| --- | --- |
| `HTTP 401: Invalid HMAC signature` | `security.token` trong `client/config.json` không khớp với `server_config.json`. Kiểm tra lại cả 2 file. |
| `HTTP 401: Timestamp outside allowed skew` | Đồng hồ giữa client và server lệch quá 5 phút. Đồng bộ NTP (`sudo timedatectl set-ntp true`). |
| `CERTIFICATE_VERIFY_FAILED` | Thiếu `tls.ca_cert` hoặc IP của server không nằm trong SAN của `server.crt`. Chạy lại `python3 deploy/gen_certs.py --ip <IP_SERVER> --force` trên server và copy `ca.crt` mới sang client. |
| `jupyterhub: [{"error": "HTTP Error 403: Forbidden"}]` | Token JupyterHub thiếu scope `list:users` / `read:users:activity` / `read:servers` hoặc chưa `tljh-config reload hub` sau khi thêm file cấu hình ở Mục 4.2. |
| `disk_mb: null` hoặc `failed_logins_recent: null` | Client không chạy bằng quyền `root`/`sudo` nên bị từ chối đọc thư mục `/home/<user>` của người khác hoặc file `/var/log/btmp`. Chạy bằng `sudo` hoặc qua `systemd` (mặc định chạy dưới `root`). |
| `Thieu psutil` | Cài bằng `pip install -r client/requirements.txt` hoặc `sudo apt install python3-psutil`. |

# AccountHubIEC Multi-Task Client Agent

Thư mục `client/` tách riêng agent thu thập tài nguyên theo từng module và **3 tầng tốc độ (multi-task)** để không bao giờ bị nghẽn bởi các tác vụ đọc ổ đĩa nặng như `du`.

## Kiến trúc 3 tầng thu thập song song

| Tầng | Collector | Chu kỳ | Cơ chế chạy |
| --- | --- | --- | --- |
| **Fast** | `system.py`, `gpu.py`, `processes.py`, `users.py` (`lastlog` 1 lần), `sessions.py` (`who`, JupyterHub) | `--interval` (mặc định `60s`) | Chạy song song qua `ThreadPoolExecutor` (`--workers 6`), hoàn thành toàn bộ trong **~1.1 giây** |
| **Medium** | `gpu_xid_errors` (`dmesg`/`journalctl`), `failed_logins` (`lastb`) | `--slow-meta-every` (mặc định `300s`) | Chạy song song khi hết TTL cache |
| **Slow (Background)** | `disk_usage.py` (`du -sm --one-file-system /home/<user>`) | `--disk-every` (mặc định `3600s`) | Chạy ngầm ở luồng riêng với mức ưu tiên thấp (`nice -n 19 ionice -c 3`) + `--disk-workers 3`, cập nhật dần vào cache từng user nên **không bao giờ block báo cáo nhanh** |

## Cấu trúc thư mục

```text
client/
├── run.py                  # Script khởi chạy trực tiếp từ bất kỳ thư mục nào
├── main.py / __main__.py   # CLI entrypoint & vòng lặp gửi báo cáo
├── config.py               # Cấu hình CLI & ngưỡng cảnh báo
├── scheduler.py            # Bộ điều phối multi-task (ThreadPoolExecutor + background cache)
├── alerts.py               # Tổng hợp cảnh báo [HEAVY], [GPU-IDLE], [DISK-FULL]...
├── sender.py               # Gửi POST JSON kèm retry/backoff & Bearer token
├── utils.py                # Chạy subprocess an toàn kèm nice/ionice
├── requirements.txt        # psutil
└── collectors/
    ├── system.py           # CPU, RAM, swap, load avg, uptime, disk partitions, jupyterhub service
    ├── gpu.py              # nvidia-smi (GPU + VRAM theo PID) & lỗi Xid trong dmesg
    ├── processes.py        # Tài nguyên theo account, GPU-IDLE, zombie, top_procs
    ├── sessions.py         # Online (who), failed logins (lastb), JupyterHub API
    ├── users.py            # Danh sách /home & parse lastlog 1 lần duy nhất
    └── disk_usage.py       # Worker quet du /home chạy ngầm song song
```

## Cài đặt & Chạy (cấu hình qua `client/config.json`)

```bash
pip install -r client/requirements.txt
cp client/config.example.json client/config.json
chmod 600 client/config.json
```

Chỉnh sửa `client/config.json` (điền `server`, `tls.ca_cert`, `security.token` khớp với `server_config.json`, và `tls.client_cert`/`tls.client_key` nếu server bật mTLS), sau đó chạy không cần truyền bí mật qua argv:

```bash
# Chạy liên tục (tự đọc client/config.json)
sudo python3 client/run.py

# Hoặc chỉ định file config riêng
sudo python3 client/run.py --config /etc/accounthub/client_config.json

# Chạy thử 1 lần (--once)
sudo python3 client/run.py --once
```


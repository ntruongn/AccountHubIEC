#!/usr/bin/env python3
"""
client_bot.py - Agent giam sat tai nguyen may chu dung chung (JupyterHub/TLJH).

Thu thap:
  - User: thu muc /home, lan dang nhap cuoi, so ngay khong hoat dong, dung luong disk
  - Online hien tai (who), so lan dang nhap that bai (neu co quyen)
  - Tai nguyen theo account: CPU, RAM, so tien trinh, zombie, tuoi tien trinh, VRAM GPU
  - GPU: nhiet do, util, VRAM, cong suat; canh bao "chiem VRAM nhung khong dung"
  - He thong: disk, uptime, service jupyterhub, loi GPU Xid (dmesg)
  - JupyterHub (tuy chon): server nao dang idle lau
  - Tong hop "alerts" de doc nhanh

KHONG thu thap: noi dung file, lenh go, doi so dong lenh cua tien trinh.

Cai dat:  pip install psutil
Chay thu: python client_bot.py --once          (nen dung sudo de thay du tien trinh/disk)
Chay that: python client_bot.py --server http://IP:8000/api/report --token SECRET --interval 60
"""
import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

try:
    import psutil
except ImportError:
    sys.exit("Thieu psutil. Cai bang: pip install psutil")

HOME_DIR = "/home"
_DISK_CACHE = {"ts": 0.0, "data": {}}   # du cham -> cache theo --disk-every


def run(cmd, timeout=10):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- users
def last_login(username):
    """Tra ve (chuoi_hien_thi, so_ngay_khong_hoat_dong | None)."""
    lines = run(["lastlog", "-u", username]).splitlines()
    if len(lines) < 2 or "Never logged in" in lines[-1]:
        return None, None
    parts = lines[-1].split(None, 1)
    text = parts[1].strip() if len(parts) > 1 else None
    days = None
    if text:
        try:  # "... Mon Nov 24 10:00:00 +0700 2026" -> lay 5 token cuoi
            dt = datetime.strptime(" ".join(text.split()[-5:]), "%b %d %H:%M:%S %z %Y")
            days = (datetime.now(timezone.utc) - dt).days
        except ValueError:
            pass
    return text, days


def disk_usage_by_user(names, every):
    """du -sm tung /home/<user>; cache de khong lam nang may."""
    now = time.time()
    if now - _DISK_CACHE["ts"] < every and _DISK_CACHE["data"]:
        return _DISK_CACHE["data"]
    data = {}
    for n in names:
        out = run(["du", "-sm", "--one-file-system", os.path.join(HOME_DIR, n)], timeout=300)
        data[n] = int(out.split()[0]) if out and out.split()[0].isdigit() else None
    _DISK_CACHE.update(ts=now, data=data)
    return data


def collect_users(args):
    try:
        names = sorted(n for n in os.listdir(HOME_DIR)
                       if os.path.isdir(os.path.join(HOME_DIR, n)))
    except OSError:
        names = []
    disk = disk_usage_by_user(names, args.disk_every)
    users = []
    for n in names:
        text, days = last_login(n)
        users.append({
            "username": n,
            "home": os.path.join(HOME_DIR, n),
            "last_login": text,
            "days_inactive": days,
            "disk_mb": disk.get(n),
        })
    return users


def collect_online():
    return [{"user": u.name, "terminal": u.terminal, "host": u.host,
             "since": datetime.fromtimestamp(u.started).isoformat(timespec="seconds")}
            for u in psutil.users()]


def failed_logins():
    """Dem so dong lastb (can root). Tra ve None neu khong doc duoc."""
    out = run(["lastb", "-n", "200"])
    if not out:
        return None
    return sum(1 for l in out.splitlines() if l.strip() and not l.startswith(("btmp", "wtmp")))


# ---------------------------------------------------------------- GPU
def collect_gpu():
    """Tra ve (gpus, gpu_mem_by_pid)."""
    gpus, by_pid = [], {}
    q = run(["nvidia-smi",
             "--query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,"
             "memory.total,power.draw,power.limit",
             "--format=csv,noheader,nounits"])

    def num(x):
        try:
            return float(x)
        except ValueError:
            return None

    for row in q.splitlines():
        f = [x.strip() for x in row.split(",")]
        if len(f) == 8:
            gpus.append({
                "index": int(f[0]), "name": f[1], "temp_c": num(f[2]),
                "util_pct": num(f[3]), "mem_used_mb": num(f[4]), "mem_total_mb": num(f[5]),
                "power_w": num(f[6]), "power_limit_w": num(f[7]),
            })
    p = run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader,nounits"])
    for row in p.splitlines():
        f = [x.strip() for x in row.split(",")]
        if len(f) == 2 and f[0].isdigit() and f[1].isdigit():
            by_pid[int(f[0])] = int(f[1])
    return gpus, by_pid


def gpu_xid_errors():
    """Cac dong Xid gan nhat trong dmesg (can quyen doc dmesg)."""
    out = run(["dmesg", "--ctime"]) or run(["journalctl", "-k", "-n", "2000", "--no-pager"])
    lines = [l for l in out.splitlines() if "NVRM: Xid" in l or " Xid" in l]
    return lines[-5:]


# ---------------------------------------------------------------- usage per account
def collect_usage(gpu_by_pid, args):
    procs = list(psutil.process_iter(["pid", "username", "name", "status", "create_time"]))
    for p in procs:                      # mo ao cpu_percent
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(1.0)

    now = time.time()
    agg = defaultdict(lambda: {"cpu_pct": 0.0, "mem_mb": 0.0, "procs": 0, "zombies": 0,
                               "gpu_mem_mb": 0, "gpu_idle_mb": 0, "gpu_idle_pids": [],
                               "oldest_age_h": 0.0, "top_procs": []})
    for p in procs:
        try:
            user = p.info["username"] or "unknown"
            a = agg[user]
            if p.info["status"] == psutil.STATUS_ZOMBIE:
                a["zombies"] += 1
                continue
            cpu = p.cpu_percent(None)
            rss = p.memory_info().rss / 1024 / 1024
            gpu = gpu_by_pid.get(p.pid, 0)
            age_h = (now - p.info["create_time"]) / 3600
            a["cpu_pct"] += cpu
            a["mem_mb"] += rss
            a["procs"] += 1
            a["gpu_mem_mb"] += gpu
            a["oldest_age_h"] = max(a["oldest_age_h"], age_h)
            # heuristic: giu VRAM, gan nhu khong dung CPU, chay du lau -> nghi bo quen
            if gpu > 0 and cpu < 1.0 and age_h * 60 >= args.gpu_idle_minutes:
                a["gpu_idle_mb"] += gpu
                a["gpu_idle_pids"].append(p.pid)
            a["top_procs"].append((cpu + rss / 100 + gpu / 10, p.pid, p.info["name"],
                                   cpu, rss, gpu, age_h))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    result = []
    for user, a in agg.items():
        a["top_procs"].sort(reverse=True)
        result.append({
            "username": user,
            "cpu_pct": round(a["cpu_pct"], 1),
            "mem_mb": round(a["mem_mb"], 1),
            "procs": a["procs"],
            "zombies": a["zombies"],
            "oldest_proc_age_h": round(a["oldest_age_h"], 1),
            "gpu_mem_mb": a["gpu_mem_mb"],
            "gpu_idle_mb": a["gpu_idle_mb"],
            "gpu_idle_pids": a["gpu_idle_pids"],
            "top_procs": [
                {"pid": t[1], "name": t[2], "cpu_pct": round(t[3], 1),
                 "mem_mb": round(t[4], 1), "gpu_mem_mb": t[5], "age_h": round(t[6], 1)}
                for t in a["top_procs"][:3]
            ],
        })
    result.sort(key=lambda x: (x["gpu_mem_mb"], x["mem_mb"], x["cpu_pct"]), reverse=True)
    return result


def flag_heavy(usage, args):
    for u in usage:
        reasons = []
        if u["cpu_pct"] >= args.cpu_threshold:
            reasons.append(f"CPU {u['cpu_pct']}% >= {args.cpu_threshold}%")
        if u["mem_mb"] >= args.mem_threshold_gb * 1024:
            reasons.append(f"RAM {u['mem_mb']/1024:.1f}GB >= {args.mem_threshold_gb}GB")
        if u["gpu_mem_mb"] >= args.gpu_threshold_mb:
            reasons.append(f"GPU {u['gpu_mem_mb']}MB >= {args.gpu_threshold_mb}MB")
        u["heavy"] = bool(reasons)
        u["heavy_reasons"] = reasons
    return usage


# ---------------------------------------------------------------- system / hub
def collect_system():
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    disks = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay"):
            continue
        try:
            du = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append({"mount": part.mountpoint, "total_gb": round(du.total / 2**30, 1),
                      "used_pct": du.percent})
    return {
        "cpu_pct": psutil.cpu_percent(None),
        "cpu_count": psutil.cpu_count(),
        "load_avg": os.getloadavg(),
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
        "mem_total_mb": vm.total // 1024 // 1024,
        "mem_used_mb": vm.used // 1024 // 1024,
        "mem_available_mb": vm.available // 1024 // 1024,
        "swap_used_mb": sw.used // 1024 // 1024,
        "swap_total_mb": sw.total // 1024 // 1024,
        "disks": disks,
        "jupyterhub_service": run(["systemctl", "is-active", "jupyterhub"]) or "unknown",
    }


def collect_hub(args):
    """Tuy chon: lay last_activity tu JupyterHub API (can --hub-url va --hub-token)."""
    if not args.hub_url or not args.hub_token:
        return []
    req = urllib.request.Request(args.hub_url.rstrip("/") + "/hub/api/users",
                                 headers={"Authorization": f"token {args.hub_token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            users = json.load(r)
    except Exception as e:  # noqa: BLE001
        return [{"error": str(e)}]
    out = []
    now = datetime.now(timezone.utc)
    for u in users:
        idle_h = None
        la = u.get("last_activity")
        if la:
            dt = datetime.fromisoformat(la.replace("Z", "+00:00"))
            idle_h = round((now - dt).total_seconds() / 3600, 1)
        out.append({"name": u["name"], "system_user": "jupyter-" + u["name"],
                    "server_running": bool(u.get("servers") or u.get("server")),
                    "last_activity": la, "idle_hours": idle_h})
    return out


# ---------------------------------------------------------------- alerts
def build_alerts(report, args):
    alerts = []
    for u in report["usage_by_account"]:
        if u["heavy"]:
            alerts.append(f"[HEAVY] {u['username']}: " + "; ".join(u["heavy_reasons"]))
        if u["gpu_idle_mb"] > 0:
            alerts.append(f"[GPU-IDLE] {u['username']} giữ {u['gpu_idle_mb']}MB VRAM nhưng "
                          f"CPU ~0 (PID {u['gpu_idle_pids']}) - có thể đã bỏ quên")
        if u["zombies"] >= args.zombie_threshold:
            alerts.append(f"[ZOMBIE] {u['username']} có {u['zombies']} tiến trình zombie")
    for usr in report["users"]:
        d, disk = usr["days_inactive"], usr["disk_mb"]
        if disk and disk >= args.disk_threshold_gb * 1024:
            alerts.append(f"[DISK] {usr['username']} đang dùng {disk/1024:.1f}GB")
        if d is not None and d >= args.inactive_days and disk and disk > 1024:
            alerts.append(f"[INACTIVE] {usr['username']} đã {d} ngày không đăng nhập, "
                          f"vẫn chiếm {disk/1024:.1f}GB")
    for dk in report["system"]["disks"]:
        if dk["used_pct"] >= 90:
            alerts.append(f"[DISK-FULL] {dk['mount']} đang dùng {dk['used_pct']}%")
    if report["system"]["jupyterhub_service"] not in ("active", "unknown"):
        alerts.append(f"[SERVICE] dịch vụ jupyterhub: {report['system']['jupyterhub_service']}")
    for g in report["gpus"]:
        if g["temp_c"] and g["temp_c"] >= 85:
            alerts.append(f"[GPU-TEMP] GPU{g['index']} nhiệt độ cao {g['temp_c']}°C")
    if report["gpu_xid_errors"]:
        alerts.append(f"[GPU-XID] {len(report['gpu_xid_errors'])} lỗi Xid gần đây trong dmesg")
    for h in report["jupyterhub"]:
        if h.get("server_running") and (h.get("idle_hours") or 0) >= args.hub_idle_hours:
            alerts.append(f"[HUB-IDLE] {h['name']} server đang chạy nhưng không hoạt động {h['idle_hours']} giờ")
    return alerts


# ---------------------------------------------------------------- report
def build_report(args):
    gpus, gpu_by_pid = collect_gpu()
    usage = flag_heavy(collect_usage(gpu_by_pid, args), args)
    report = {
        "host": socket.gethostname(),
        "os": platform.platform(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system": collect_system(),
        "gpus": gpus,
        "gpu_xid_errors": gpu_xid_errors(),
        "users": collect_users(args),
        "online_now": collect_online(),
        "failed_logins_recent": failed_logins(),
        "jupyterhub": collect_hub(args),
        "usage_by_account": usage,
        "heavy_accounts": [u["username"] for u in usage if u["heavy"]],
    }
    report["alerts"] = build_alerts(report, args)
    return report


def send(report, url, token):
    data = json.dumps(report).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}" if token else "",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser(description="Client bot giam sat tai nguyen")
    ap.add_argument("--server", help="URL nhan bao cao (POST JSON)")
    ap.add_argument("--token", default=os.environ.get("BOT_TOKEN", ""))
    ap.add_argument("--interval", type=int, default=60, help="giay giua 2 lan bao cao")
    ap.add_argument("--once", action="store_true", help="chay 1 lan va in JSON")
    ap.add_argument("--cpu-threshold", type=float, default=400.0, help="tong %%CPU (100 = 1 core)")
    ap.add_argument("--mem-threshold-gb", type=float, default=32.0)
    ap.add_argument("--gpu-threshold-mb", type=int, default=8000)
    ap.add_argument("--gpu-idle-minutes", type=int, default=60,
                    help="giu VRAM + CPU~0 qua so phut nay thi bao GPU-IDLE")
    ap.add_argument("--zombie-threshold", type=int, default=5)
    ap.add_argument("--disk-threshold-gb", type=float, default=200.0)
    ap.add_argument("--disk-every", type=int, default=3600,
                    help="giay giua 2 lan do disk tung user (du cham)")
    ap.add_argument("--inactive-days", type=int, default=90)
    ap.add_argument("--hub-url", help="vd http://localhost:8000 (tuy chon)")
    ap.add_argument("--hub-token", default=os.environ.get("HUB_TOKEN", ""))
    ap.add_argument("--hub-idle-hours", type=float, default=24.0)
    args = ap.parse_args()

    if args.once or not args.server:
        print(json.dumps(build_report(args), indent=2, ensure_ascii=False))
        # save to file client_bot_report.json
        with open("client_bot_report.json", "w", encoding="utf-8") as f:
            json.dump(build_report(args), f, indent=2, ensure_ascii=False)
        return

    while True:
        try:
            status = send(build_report(args), args.server, args.token)
            print(f"[{datetime.now():%H:%M:%S}] sent, HTTP {status}", flush=True)
        except Exception as e:  # noqa: BLE001 - bot phai song sot khi loi mang
            print(f"[{datetime.now():%H:%M:%S}] error: {e}", file=sys.stderr, flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
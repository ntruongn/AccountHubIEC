"""Fast collector: per-account CPU, RAM, VRAM, zombie, GPU-idle, and top processes."""
from __future__ import annotations

import time
from collections import defaultdict

import psutil

from ..config import ClientConfig


def collect_usage_by_account(gpu_by_pid: dict[int, int], cfg: ClientConfig) -> list[dict]:
    procs = list(psutil.process_iter(["pid", "username", "name", "status", "create_time"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(cfg.cpu_sample_sec)

    now = time.time()
    agg = defaultdict(
        lambda: {
            "cpu_pct": 0.0,
            "mem_mb": 0.0,
            "procs": 0,
            "zombies": 0,
            "gpu_mem_mb": 0,
            "gpu_idle_mb": 0,
            "gpu_idle_pids": [],
            "oldest_age_h": 0.0,
            "top_procs": [],
        }
    )

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

            if gpu > 0 and cpu < 1.0 and age_h * 60 >= cfg.gpu_idle_minutes:
                a["gpu_idle_mb"] += gpu
                a["gpu_idle_pids"].append(p.pid)

            a["top_procs"].append((cpu + rss / 100 + gpu / 10, p.pid, p.info["name"], cpu, rss, gpu, age_h))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    result: list[dict] = []
    for user, a in agg.items():
        a["top_procs"].sort(reverse=True)
        entry = {
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
                {
                    "pid": t[1],
                    "name": t[2],
                    "cpu_pct": round(t[3], 1),
                    "mem_mb": round(t[4], 1),
                    "gpu_mem_mb": t[5],
                    "age_h": round(t[6], 1),
                }
                for t in a["top_procs"][:5]
            ],
        }
        reasons = []
        if entry["cpu_pct"] >= cfg.cpu_threshold:
            reasons.append(f"CPU {entry['cpu_pct']}% >= {cfg.cpu_threshold}%")
        if entry["mem_mb"] >= cfg.mem_threshold_gb * 1024:
            reasons.append(f"RAM {entry['mem_mb'] / 1024:.1f}GB >= {cfg.mem_threshold_gb}GB")
        if entry["gpu_mem_mb"] >= cfg.gpu_threshold_mb:
            reasons.append(f"GPU {entry['gpu_mem_mb']}MB >= {cfg.gpu_threshold_mb}MB")
        entry["heavy"] = bool(reasons)
        entry["heavy_reasons"] = reasons
        result.append(entry)

    result.sort(key=lambda x: (x["gpu_mem_mb"], x["mem_mb"], x["cpu_pct"]), reverse=True)
    return result


"""Fast collector: system CPU, RAM, swap, load avg, uptime, disk partitions, jupyterhub service."""
from __future__ import annotations

import os
import time

import psutil

from ..utils import run_cmd


def collect_system() -> dict:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    disks = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay"):
            continue
        try:
            du = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append(
            {
                "mount": part.mountpoint,
                "total_gb": round(du.total / 2**30, 1),
                "used_pct": du.percent,
            }
        )
    return {
        "cpu_pct": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count(),
        "load_avg": list(os.getloadavg()),
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
        "mem_total_mb": vm.total // 1024 // 1024,
        "mem_used_mb": vm.used // 1024 // 1024,
        "mem_available_mb": vm.available // 1024 // 1024,
        "swap_used_mb": sw.used // 1024 // 1024,
        "swap_total_mb": sw.total // 1024 // 1024,
        "disks": disks,
        "jupyterhub_service": run_cmd(["systemctl", "is-active", "jupyterhub"], timeout=5) or "unknown",
    }


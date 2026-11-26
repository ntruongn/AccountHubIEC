"""Multi-task orchestrator combining fast parallel tasks, medium cached tasks, and slow background disk scan."""
from __future__ import annotations

import platform
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .alerts import build_alerts
from .collectors.disk_usage import HomeDiskScanner
from .collectors.gpu import collect_gpu, collect_gpu_xid_errors
from .collectors.processes import collect_usage_by_account
from .collectors.sessions import collect_failed_logins, collect_jupyterhub, collect_online
from .collectors.system import collect_system
from .collectors.users import collect_users_meta
from .config import ClientConfig
from .utils import utc_now_iso


class MultiTaskCollector:
    """
    3-tier concurrent collector:
      - Tier 1 (Fast, every --interval): system, gpu, processes, online, users(lastlog), jupyterhub
        -> Executed concurrently in ThreadPoolExecutor (~1.1s total, bounded by 1.0s CPU sample).
      - Tier 2 (Medium, every --slow-meta-every): dmesg Xid errors & lastb failed logins
        -> Refreshed in parallel when cache TTL expires.
      - Tier 3 (Slow, every --disk-every): /home/<user> `du -sm`
        -> Runs asynchronously in HomeDiskScanner background thread pool with nice/ionice,
           updating cache incrementally so fast reports NEVER block on disk I/O.
    """

    def __init__(self, cfg: ClientConfig):
        self.cfg = cfg
        self.disk_scanner = HomeDiskScanner(
            home_dir=cfg.home_dir,
            every_sec=cfg.disk_every,
            workers=cfg.disk_workers,
            timeout=cfg.disk_timeout,
        )
        self._meta_lock = threading.Lock()
        self._meta_ts = 0.0
        self._cached_xid: list[str] = []
        self._cached_failed_logins: int | None = None

    def start(self) -> None:
        self.disk_scanner.start()

    def stop(self) -> None:
        self.disk_scanner.stop()

    def _timed(self, fn, *args):
        t0 = time.monotonic()
        result = fn(*args)
        return result, round((time.monotonic() - t0) * 1000, 1)

    def _collect_gpu_and_procs(self) -> tuple[list[dict], list[dict], dict[str, float]]:
        t0 = time.monotonic()
        gpus, gpu_by_pid = collect_gpu()
        t_gpu = round((time.monotonic() - t0) * 1000, 1)

        t1 = time.monotonic()
        usage = collect_usage_by_account(gpu_by_pid, self.cfg)
        t_proc = round((time.monotonic() - t1) * 1000, 1)
        return gpus, usage, {"gpu_ms": t_gpu, "processes_ms": t_proc}

    def collect_report(self, wait_disk_sec: float = 0.0) -> dict:
        total_start = time.monotonic()
        if wait_disk_sec > 0:
            self.disk_scanner.wait_initial(wait_disk_sec)

        now_mono = time.monotonic()
        need_medium = (now_mono - self._meta_ts) >= self.cfg.slow_meta_every or self._meta_ts == 0.0
        disk_map, disk_meta = self.disk_scanner.snapshot()

        timings_ms: dict[str, float] = {}
        with ThreadPoolExecutor(max_workers=self.cfg.workers, thread_name_prefix="collector") as pool:
            f_gpu_procs = pool.submit(self._collect_gpu_and_procs)
            f_system = pool.submit(self._timed, collect_system)
            f_users = pool.submit(self._timed, collect_users_meta, self.cfg.home_dir, disk_map)
            f_online = pool.submit(self._timed, collect_online)
            f_hub = pool.submit(self._timed, collect_jupyterhub, self.cfg)

            f_xid = pool.submit(self._timed, collect_gpu_xid_errors) if need_medium else None
            f_failed = pool.submit(self._timed, collect_failed_logins) if need_medium else None

            gpus, usage, gp_times = f_gpu_procs.result()
            timings_ms.update(gp_times)

            system_info, timings_ms["system_ms"] = f_system.result()
            users_info, timings_ms["users_ms"] = f_users.result()
            online_info, timings_ms["online_ms"] = f_online.result()
            hub_info, timings_ms["jupyterhub_ms"] = f_hub.result()

            if need_medium and f_xid and f_failed:
                xid_val, timings_ms["xid_ms"] = f_xid.result()
                failed_val, timings_ms["failed_logins_ms"] = f_failed.result()
                with self._meta_lock:
                    self._cached_xid = xid_val
                    self._cached_failed_logins = failed_val
                    self._meta_ts = time.monotonic()

        # Re-read latest disk map in case background worker finished more users during the 1s sample
        latest_disk_map, disk_meta = self.disk_scanner.snapshot()
        for u in users_info:
            if u["username"] in latest_disk_map:
                u["disk_mb"] = latest_disk_map[u["username"]]

        with self._meta_lock:
            xid_errors = list(self._cached_xid)
            failed_logins = self._cached_failed_logins

        timings_ms["total_ms"] = round((time.monotonic() - total_start) * 1000, 1)

        report = {
            "host": socket.gethostname(),
            "os": platform.platform(),
            "timestamp": utc_now_iso(),
            "system": system_info,
            "gpus": gpus,
            "gpu_xid_errors": xid_errors,
            "users": users_info,
            "online_now": online_info,
            "failed_logins_recent": failed_logins,
            "jupyterhub": hub_info,
            "usage_by_account": usage,
            "heavy_accounts": [u["username"] for u in usage if u["heavy"]],
            "collector_meta": {
                "mode": "multi-task",
                "timings_ms": timings_ms,
                "disk_scan": disk_meta,
            },
        }
        report["alerts"] = build_alerts(report, self.cfg)
        return report


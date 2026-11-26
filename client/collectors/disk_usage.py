"""Slow background multi-task collector for /home/<user> disk usage (`du`).

Runs asynchronously with low CPU/IO priority (`nice -n 19 ionice -c 3`) and a bounded
ThreadPoolExecutor so slow `du` scans NEVER block fast system/GPU telemetry reporting.
Updates shared cache incrementally as each user directory finishes scanning.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..utils import run_cmd, utc_now_iso
from .users import list_home_users


def _measure_dir_mb(path: str, timeout: int) -> int | None:
    """Measure a single user home directory in MiB with low I/O priority."""
    out = run_cmd(["du", "-sm", "--one-file-system", path], timeout=timeout, low_priority=True)
    if not out:
        return None
    first = out.split()[0]
    return int(first) if first.isdigit() else None


class HomeDiskScanner:
    def __init__(self, home_dir: str = "/home", every_sec: int = 3600, workers: int = 3, timeout: int = 300):
        self.home_dir = home_dir
        self.every_sec = every_sec
        self.workers = workers
        self.timeout = timeout

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._first_done = threading.Event()
        self._thread: threading.Thread | None = None

        self._data: dict[str, int | None] = {}
        self._status = "idle"  # idle | scanning | ready
        self._scanned = 0
        self._total = 0
        self._last_completed_at: str | None = None
        self._last_duration_s: float | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="home-disk-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def wait_initial(self, timeout_sec: float) -> bool:
        """Optionally wait up to `timeout_sec` for the first scan to finish (used by --once)."""
        if timeout_sec <= 0:
            return False
        return self._first_done.wait(timeout=timeout_sec)

    def snapshot(self) -> tuple[dict[str, int | None], dict]:
        with self._lock:
            data_copy = dict(self._data)
            meta = {
                "status": self._status,
                "scanned": self._scanned,
                "total": self._total,
                "last_completed_at": self._last_completed_at,
                "last_duration_s": self._last_duration_s,
            }
        return data_copy, meta

    def _run_once(self) -> None:
        names = list_home_users(self.home_dir)
        started = time.monotonic()
        with self._lock:
            self._status = "scanning"
            self._scanned = 0
            self._total = len(names)

        if not names:
            with self._lock:
                self._status = "ready"
                self._last_completed_at = utc_now_iso()
                self._last_duration_s = round(time.monotonic() - started, 2)
            self._first_done.set()
            return

        active_set = set(names)
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="du-worker") as pool:
            future_map = {
                pool.submit(_measure_dir_mb, os.path.join(self.home_dir, n), self.timeout): n for n in names
            }
            for fut in as_completed(future_map):
                if self._stop_event.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    return
                user = future_map[fut]
                try:
                    mb = fut.result()
                except Exception:  # noqa: BLE001
                    mb = None
                with self._lock:
                    self._data[user] = mb
                    self._scanned += 1

        with self._lock:
            # Remove deleted home directories from cache
            for old_user in list(self._data.keys()):
                if old_user not in active_set:
                    del self._data[old_user]
            self._status = "ready"
            self._last_completed_at = utc_now_iso()
            self._last_duration_s = round(time.monotonic() - started, 2)
        self._first_done.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._run_once()
            if self._stop_event.wait(timeout=self.every_sec):
                break


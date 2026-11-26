"""Client configuration loader driven by JSON config file (no secrets in CLI argv)."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent
DEFAULT_CLIENT_CONFIG = CLIENT_DIR / "config.json"


def _resolve_path(base_dir: Path, value: str) -> str:
    val = (value or "").strip()
    if not val:
        return ""
    p = Path(val)
    return str(p if p.is_absolute() else (base_dir / p).resolve())


def _warn_if_loose_permissions(path: Path, has_secrets: bool) -> None:
    if not has_secrets or os.name != "posix":
        return
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            print(
                f"[WARN] File config {path} dang co quyen {oct(mode)} (qua rong). "
                f"Nen chay: chmod 600 {path}",
                file=sys.stderr,
            )
    except OSError:
        pass


@dataclass
class ClientConfig:
    server: str | None = None
    token: str = ""
    interval: int = 60
    once: bool = False
    output_file: str = "client_bot_report.json"
    config_path: str = ""

    # Transport security (TLS / mTLS / HMAC)
    ca_cert: str = ""
    client_cert: str = ""
    client_key: str = ""
    insecure_tls: bool = False
    use_hmac: bool = True

    # Multi-task / concurrency tuning
    workers: int = 6
    disk_workers: int = 3
    disk_every: int = 3600
    disk_timeout: int = 300
    disk_once_wait: int = 15
    slow_meta_every: int = 300
    cpu_sample_sec: float = 1.0
    home_dir: str = "/home"

    # Alert thresholds
    cpu_threshold: float = 400.0
    mem_threshold_gb: float = 32.0
    gpu_threshold_mb: int = 8000
    gpu_idle_minutes: int = 60
    zombie_threshold: int = 5
    disk_threshold_gb: float = 200.0
    inactive_days: int = 90

    # Optional JupyterHub API
    hub_url: str | None = None
    hub_token: str = ""
    hub_idle_hours: float = 24.0

    # HTTP retry
    http_timeout: int = 15
    http_retries: int = 3

    @classmethod
    def from_file(cls, config_path: str | Path | None = None, once: bool = False) -> "ClientConfig":
        cfg_file = Path(config_path) if config_path else DEFAULT_CLIENT_CONFIG
        cfg_file = cfg_file.resolve()
        if not cfg_file.is_file():
            raise FileNotFoundError(
                f"Khong tim thay file cau hinh client: {cfg_file}\n"
                f"Hay tao tu mau: cp {CLIENT_DIR / 'config.example.json'} {cfg_file} && chmod 600 {cfg_file}"
            )

        with cfg_file.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"File cau hinh khong hop le (phai la JSON object): {cfg_file}")

        cfg_dir = cfg_file.parent
        tls = raw.get("tls") or {}
        sec = raw.get("security") or {}
        sched = raw.get("schedule") or {}
        thr = raw.get("thresholds") or {}
        hub = raw.get("jupyterhub") or {}
        http = raw.get("http") or {}

        token = str(sec.get("token") or "")
        hub_token = str(hub.get("token") or "")
        _warn_if_loose_permissions(cfg_file, has_secrets=bool(token or hub_token))

        return cls(
            server=(str(raw.get("server") or "").strip() or None),
            token=token,
            interval=max(5, int(sched.get("interval", 60))),
            once=bool(once),
            output_file=str(raw.get("output_file") or "client_bot_report.json"),
            config_path=str(cfg_file),
            ca_cert=_resolve_path(cfg_dir, str(tls.get("ca_cert") or "")),
            client_cert=_resolve_path(cfg_dir, str(tls.get("client_cert") or "")),
            client_key=_resolve_path(cfg_dir, str(tls.get("client_key") or "")),
            insecure_tls=bool(tls.get("insecure", False)),
            use_hmac=bool(sec.get("use_hmac", True)),
            workers=max(2, int(sched.get("workers", 6))),
            disk_workers=max(1, int(sched.get("disk_workers", 3))),
            disk_every=max(60, int(sched.get("disk_every", 3600))),
            disk_timeout=max(10, int(sched.get("disk_timeout", 300))),
            disk_once_wait=max(0, int(sched.get("disk_once_wait", 15))),
            slow_meta_every=max(30, int(sched.get("slow_meta_every", 300))),
            cpu_sample_sec=max(0.1, float(sched.get("cpu_sample_sec", 1.0))),
            home_dir=str(sched.get("home_dir") or "/home"),
            cpu_threshold=float(thr.get("cpu_pct", 400.0)),
            mem_threshold_gb=float(thr.get("mem_gb", 32.0)),
            gpu_threshold_mb=int(thr.get("gpu_mb", 8000)),
            gpu_idle_minutes=int(thr.get("gpu_idle_minutes", 60)),
            zombie_threshold=int(thr.get("zombies", 5)),
            disk_threshold_gb=float(thr.get("disk_gb", 200.0)),
            inactive_days=int(thr.get("inactive_days", 90)),
            hub_url=(str(hub.get("url") or "").strip() or None),
            hub_token=hub_token,
            hub_idle_hours=float(hub.get("idle_hours", 24.0)),
            http_timeout=max(3, int(http.get("timeout", 15))),
            http_retries=max(1, int(http.get("retries", 3))),
        )


def load_config_from_cli(argv: list[str] | None = None) -> ClientConfig:
    ap = argparse.ArgumentParser(
        description="AccountHubIEC Multi-Task Client Agent (doc cau hinh tu file JSON)"
    )
    ap.add_argument(
        "-c",
        "--config",
        default=str(DEFAULT_CLIENT_CONFIG),
        help=f"Duong dan file cau hinh JSON (mac dinh: client/{DEFAULT_CLIENT_CONFIG.name})",
    )
    ap.add_argument("--once", action="store_true", help="Thu thap 1 lan, in JSON va luu file")
    ns = ap.parse_args(argv)
    return ClientConfig.from_file(ns.config, once=ns.once)

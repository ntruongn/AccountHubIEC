"""Fast/Medium collectors: active TTY/SSH sessions, failed logins (lastb), JupyterHub API."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

import psutil

from ..config import ClientConfig
from ..utils import run_cmd


def collect_online() -> list[dict]:
    return [
        {
            "user": u.name,
            "terminal": u.terminal,
            "host": u.host,
            "since": datetime.fromtimestamp(u.started).isoformat(timespec="seconds"),
        }
        for u in psutil.users()
    ]


def collect_failed_logins() -> int | None:
    """Count recent failed logins via lastb (requires root). Returns None if unreadable."""
    out = run_cmd(["lastb", "-n", "200"], timeout=8)
    if not out:
        return None
    return sum(1 for line in out.splitlines() if line.strip() and not line.startswith(("btmp", "wtmp")))


def collect_jupyterhub(cfg: ClientConfig) -> list[dict]:
    if not cfg.hub_url or not cfg.hub_token:
        return []
    req = urllib.request.Request(
        cfg.hub_url.rstrip("/") + "/hub/api/users",
        headers={"Authorization": f"token {cfg.hub_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            users = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]

    out: list[dict] = []
    now = datetime.now(timezone.utc)
    for u in users:
        idle_h = None
        la = u.get("last_activity")
        if la:
            try:
                dt = datetime.fromisoformat(la.replace("Z", "+00:00"))
                idle_h = round((now - dt).total_seconds() / 3600, 1)
            except ValueError:
                pass
        out.append(
            {
                "name": u["name"],
                "system_user": "jupyter-" + u["name"],
                "server_running": bool(u.get("servers") or u.get("server")),
                "last_activity": la,
                "idle_hours": idle_h,
            }
        )
    return out


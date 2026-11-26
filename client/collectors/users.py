"""Fast user metadata collector: lists /home and parses a single `lastlog` invocation."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from ..utils import run_cmd


def list_home_users(home_dir: str = "/home") -> list[str]:
    try:
        return sorted(n for n in os.listdir(home_dir) if os.path.isdir(os.path.join(home_dir, n)))
    except OSError:
        return []


def _parse_lastlog_table(raw: str) -> dict[str, tuple[str | None, int | None]]:
    """Parse full `lastlog` output once instead of spawning N subprocesses."""
    now = datetime.now(timezone.utc)
    result: dict[str, tuple[str | None, int | None]] = {}
    lines = raw.splitlines()
    for line in lines[1:]:  # skip header
        if not line.strip():
            continue
        parts = line.split(None, 1)
        user = parts[0].strip()
        if len(parts) < 2 or "Never logged in" in parts[1]:
            result[user] = (None, None)
            continue
        text = parts[1].strip()
        days: int | None = None
        tokens = text.split()
        if len(tokens) >= 5:
            try:
                dt = datetime.strptime(" ".join(tokens[-5:]), "%b %d %H:%M:%S %z %Y")
                days = max(0, (now - dt).days)
            except ValueError:
                pass
        result[user] = (text, days)
    return result


def collect_users_meta(home_dir: str = "/home", disk_map: dict[str, int | None] | None = None) -> list[dict]:
    names = list_home_users(home_dir)
    table = _parse_lastlog_table(run_cmd(["lastlog"], timeout=10))
    disk_map = disk_map or {}

    users: list[dict] = []
    for name in names:
        text, days = table.get(name, (None, None))
        users.append(
            {
                "username": name,
                "home": os.path.join(home_dir, name),
                "last_login": text,
                "days_inactive": days,
                "disk_mb": disk_map.get(name),
            }
        )
    return users


"""SQLite WAL storage, analytics, incident lifecycle tracking, and CSV export."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

SEVERITY_MAP = {
    "DISK-FULL": "critical",
    "GPU-XID": "critical",
    "GPU-TEMP": "critical",
    "SERVICE": "critical",
    "HEAVY": "warning",
    "GPU-IDLE": "warning",
    "ZOMBIE": "warning",
    "DISK": "warning",
    "INACTIVE": "info",
    "HUB-IDLE": "info",
}

DEFAULT_SETTINGS = {
    "webhook_enabled": False,
    "webhook_url": "",
    "webhook_min_severity": "warning",
    "email_auto_enabled": False,
    "email_min_severity": "warning",
    "email_cooldown_minutes": 240,
    "email_cc_admin": True,
    "email_admin_emails": "",
    "stale_seconds": 180,
    "retain_days": 14,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def parse_alert(text: str) -> dict:
    tag = "INFO"
    message = str(text)
    if message.startswith("[") and "]" in message:
        end = message.index("]")
        tag = message[1:end].strip().upper()
        message = message[end + 1 :].strip()

    # Build a stable fingerprint so minor numeric fluctuations (e.g. 98.8% vs 98.9%) map to the same incident
    subject_token = message.split()[0] if message.split() else message
    norm = re.sub(r"\d+(\.\d+)?", "#", f"{tag}:{subject_token}").lower()
    fingerprint = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]

    return {
        "tag": tag,
        "severity": SEVERITY_MAP.get(tag, "info"),
        "message": message,
        "raw": str(text),
        "fingerprint": fingerprint,
    }


def summarize_report(report: dict) -> dict:
    system = report.get("system") or {}
    gpus = report.get("gpus") or []
    disks = system.get("disks") or []
    alerts = report.get("alerts") or []
    heavy = report.get("heavy_accounts") or []
    online = report.get("online_now") or []
    users = report.get("users") or []
    usage = report.get("usage_by_account") or []

    mem_total = float(system.get("mem_total_mb") or 0)
    mem_used = float(system.get("mem_used_mb") or 0)
    mem_used_pct = round((mem_used / mem_total) * 100, 1) if mem_total > 0 else 0.0

    gpu_utils = [float(g["util_pct"]) for g in gpus if g.get("util_pct") is not None]
    gpu_temps = [float(g["temp_c"]) for g in gpus if g.get("temp_c") is not None]
    gpu_mem_used = sum(float(g.get("mem_used_mb") or 0) for g in gpus)
    gpu_mem_total = sum(float(g.get("mem_total_mb") or 0) for g in gpus)
    gpu_util_avg = round(sum(gpu_utils) / len(gpu_utils), 1) if gpu_utils else 0.0
    gpu_temp_max = max(gpu_temps) if gpu_temps else None

    disk_pcts = [float(d.get("used_pct") or 0) for d in disks]
    disk_max_pct = max(disk_pcts) if disk_pcts else 0.0

    load_avg = system.get("load_avg") or []
    load_1m = float(load_avg[0]) if len(load_avg) >= 1 and load_avg[0] is not None else 0.0

    gpu_idle_count = sum(1 for u in usage if float(u.get("gpu_idle_mb") or 0) > 0)

    return {
        "host": str(report.get("host") or "unknown").strip() or "unknown",
        "os": str(report.get("os") or ""),
        "timestamp": str(report.get("timestamp") or utc_now_iso()),
        "cpu_pct": float(system.get("cpu_pct") or 0.0),
        "cpu_count": int(system.get("cpu_count") or 0),
        "load_1m": round(load_1m, 2),
        "uptime_h": float(system.get("uptime_h") or 0.0),
        "mem_used_mb": round(mem_used, 1),
        "mem_total_mb": round(mem_total, 1),
        "mem_used_pct": mem_used_pct,
        "gpu_count": len(gpus),
        "gpu_util_avg": gpu_util_avg,
        "gpu_mem_used_mb": round(gpu_mem_used, 1),
        "gpu_mem_total_mb": round(gpu_mem_total, 1),
        "gpu_temp_max": gpu_temp_max,
        "disk_max_pct": round(disk_max_pct, 1),
        "jupyterhub_service": str(system.get("jupyterhub_service") or "unknown"),
        "online_count": len(online),
        "users_count": len(users),
        "heavy_count": len(heavy),
        "gpu_idle_count": gpu_idle_count,
        "alerts_count": len(alerts),
    }


class MonitorStore:
    def __init__(self, db_path: str, retain_days: int = 14, max_per_host: int = 10000, stale_seconds: int = 180):
        self.db_path = db_path
        self.default_retain_days = retain_days
        self.max_per_host = max_per_host
        self.default_stale_seconds = stale_seconds
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hosts (
                    host TEXT PRIMARY KEY,
                    os TEXT,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    report_timestamp TEXT NOT NULL,
                    cpu_pct REAL,
                    cpu_count INTEGER,
                    load_1m REAL,
                    uptime_h REAL,
                    mem_used_mb REAL,
                    mem_total_mb REAL,
                    mem_used_pct REAL,
                    gpu_count INTEGER,
                    gpu_util_avg REAL,
                    gpu_mem_used_mb REAL,
                    gpu_mem_total_mb REAL,
                    gpu_temp_max REAL,
                    disk_max_pct REAL,
                    jupyterhub_service TEXT,
                    online_count INTEGER,
                    users_count INTEGER,
                    heavy_count INTEGER,
                    alerts_count INTEGER,
                    latest_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    cpu_pct REAL,
                    load_1m REAL,
                    mem_used_mb REAL,
                    mem_total_mb REAL,
                    mem_used_pct REAL,
                    gpu_util_avg REAL,
                    gpu_mem_used_mb REAL,
                    gpu_mem_total_mb REAL,
                    gpu_temp_max REAL,
                    disk_max_pct REAL,
                    online_count INTEGER,
                    heavy_count INTEGER,
                    alerts_count INTEGER,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_reports_host_received
                    ON reports(host, received_at DESC);

                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    raw TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    resolved_at TEXT,
                    occurrences INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_alert_events_host_open
                    ON alert_events(host, resolved_at);

                CREATE TABLE IF NOT EXISTS alert_acks (
                    host TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    acked_at TEXT NOT NULL,
                    acked_by TEXT NOT NULL DEFAULT 'admin',
                    note TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (host, fingerprint)
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                """
            )
            # Lightweight forward-compatible column migrations on hosts table
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(hosts)").fetchall()}
            if "note" not in cols:
                conn.execute("ALTER TABLE hosts ADD COLUMN note TEXT DEFAULT ''")
            if "gpu_idle_count" not in cols:
                conn.execute("ALTER TABLE hosts ADD COLUMN gpu_idle_count INTEGER DEFAULT 0")

            # Seed default settings if absent
            defaults = dict(DEFAULT_SETTINGS)
            defaults["stale_seconds"] = self.default_stale_seconds
            defaults["retain_days"] = self.default_retain_days
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO app_settings (key, value_json) VALUES (?, ?)",
                    (k, json.dumps(v)),
                )

    # ------------------------------------------------------------ settings
    def get_settings(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
        data = dict(DEFAULT_SETTINGS)
        for r in rows:
            try:
                data[r["key"]] = json.loads(r["value_json"])
            except json.JSONDecodeError:
                pass
        return data

    def update_settings(self, updates: dict) -> dict:
        allowed = set(DEFAULT_SETTINGS.keys())
        cleaned: dict = {}
        for k, v in updates.items():
            if k not in allowed:
                continue
            if k in ("stale_seconds", "retain_days", "email_cooldown_minutes"):
                cleaned[k] = max(1, int(v))
            elif k in ("webhook_enabled", "email_auto_enabled", "email_cc_admin"):
                cleaned[k] = bool(v)
            elif k in ("webhook_min_severity", "email_min_severity"):
                cleaned[k] = v if v in ("info", "warning", "critical") else "warning"
            elif k in ("webhook_url", "email_admin_emails"):
                cleaned[k] = str(v or "").strip()

        with self._lock, self._connect() as conn:
            for k, v in cleaned.items():
                conn.execute(
                    "INSERT INTO app_settings (key, value_json) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                    (k, json.dumps(v)),
                )
        return self.get_settings()

    # ------------------------------------------------------------ ingest
    def save_report(self, report: dict, received_at: str | None = None) -> dict:
        summary = summarize_report(report)
        now_iso = received_at or utc_now_iso()
        payload_json = json.dumps(report, ensure_ascii=False)
        parsed_alerts = [parse_alert(a) for a in (report.get("alerts") or [])]

        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO reports (
                    host, timestamp, received_at, cpu_pct, load_1m,
                    mem_used_mb, mem_total_mb, mem_used_pct,
                    gpu_util_avg, gpu_mem_used_mb, gpu_mem_total_mb, gpu_temp_max,
                    disk_max_pct, online_count, heavy_count, alerts_count, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary["host"],
                    summary["timestamp"],
                    now_iso,
                    summary["cpu_pct"],
                    summary["load_1m"],
                    summary["mem_used_mb"],
                    summary["mem_total_mb"],
                    summary["mem_used_pct"],
                    summary["gpu_util_avg"],
                    summary["gpu_mem_used_mb"],
                    summary["gpu_mem_total_mb"],
                    summary["gpu_temp_max"],
                    summary["disk_max_pct"],
                    summary["online_count"],
                    summary["heavy_count"],
                    summary["alerts_count"],
                    payload_json,
                ),
            )
            report_id = cur.lastrowid

            conn.execute(
                """
                INSERT INTO hosts (
                    host, os, first_seen, last_seen, report_timestamp,
                    cpu_pct, cpu_count, load_1m, uptime_h,
                    mem_used_mb, mem_total_mb, mem_used_pct,
                    gpu_count, gpu_util_avg, gpu_mem_used_mb, gpu_mem_total_mb, gpu_temp_max,
                    disk_max_pct, jupyterhub_service, online_count, users_count,
                    heavy_count, gpu_idle_count, alerts_count, latest_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(host) DO UPDATE SET
                    os = excluded.os,
                    last_seen = excluded.last_seen,
                    report_timestamp = excluded.report_timestamp,
                    cpu_pct = excluded.cpu_pct,
                    cpu_count = excluded.cpu_count,
                    load_1m = excluded.load_1m,
                    uptime_h = excluded.uptime_h,
                    mem_used_mb = excluded.mem_used_mb,
                    mem_total_mb = excluded.mem_total_mb,
                    mem_used_pct = excluded.mem_used_pct,
                    gpu_count = excluded.gpu_count,
                    gpu_util_avg = excluded.gpu_util_avg,
                    gpu_mem_used_mb = excluded.gpu_mem_used_mb,
                    gpu_mem_total_mb = excluded.gpu_mem_total_mb,
                    gpu_temp_max = excluded.gpu_temp_max,
                    disk_max_pct = excluded.disk_max_pct,
                    jupyterhub_service = excluded.jupyterhub_service,
                    online_count = excluded.online_count,
                    users_count = excluded.users_count,
                    heavy_count = excluded.heavy_count,
                    gpu_idle_count = excluded.gpu_idle_count,
                    alerts_count = excluded.alerts_count,
                    latest_json = excluded.latest_json
                """,
                (
                    summary["host"],
                    summary["os"],
                    now_iso,
                    now_iso,
                    summary["timestamp"],
                    summary["cpu_pct"],
                    summary["cpu_count"],
                    summary["load_1m"],
                    summary["uptime_h"],
                    summary["mem_used_mb"],
                    summary["mem_total_mb"],
                    summary["mem_used_pct"],
                    summary["gpu_count"],
                    summary["gpu_util_avg"],
                    summary["gpu_mem_used_mb"],
                    summary["gpu_mem_total_mb"],
                    summary["gpu_temp_max"],
                    summary["disk_max_pct"],
                    summary["jupyterhub_service"],
                    summary["online_count"],
                    summary["users_count"],
                    summary["heavy_count"],
                    summary["gpu_idle_count"],
                    summary["alerts_count"],
                    payload_json,
                ),
            )

            new_alerts = self._sync_alert_events_locked(conn, summary["host"], parsed_alerts, now_iso)
            acked_fps = {
                r["fingerprint"]
                for r in conn.execute("SELECT fingerprint FROM alert_acks WHERE host = ?", (summary["host"],)).fetchall()
            }
            self._prune_host_locked(conn, summary["host"])

        unacked_alerts = [a for a in parsed_alerts if a["fingerprint"] not in acked_fps]
        return {
            "id": report_id,
            "host": summary["host"],
            "received_at": now_iso,
            "alerts_count": summary["alerts_count"],
            "new_alerts": new_alerts,
            "unacked_alerts": unacked_alerts,
        }

    def _sync_alert_events_locked(
        self, conn: sqlite3.Connection, host: str, parsed_alerts: list[dict], now_iso: str
    ) -> list[dict]:
        open_rows = conn.execute(
            "SELECT id, fingerprint FROM alert_events WHERE host = ? AND resolved_at IS NULL",
            (host,),
        ).fetchall()
        open_by_fp = {r["fingerprint"]: r["id"] for r in open_rows}

        active_fps: set[str] = set()
        new_alerts: list[dict] = []

        for a in parsed_alerts:
            fp = a["fingerprint"]
            if fp in active_fps:
                continue
            active_fps.add(fp)
            if fp in open_by_fp:
                conn.execute(
                    """
                    UPDATE alert_events
                    SET last_seen_at = ?, message = ?, raw = ?, occurrences = occurrences + 1
                    WHERE id = ?
                    """,
                    (now_iso, a["message"], a["raw"], open_by_fp[fp]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO alert_events (
                        host, fingerprint, tag, severity, message, raw,
                        opened_at, last_seen_at, resolved_at, occurrences
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1)
                    """,
                    (host, fp, a["tag"], a["severity"], a["message"], a["raw"], now_iso, now_iso),
                )
                new_alerts.append(a)

        for fp, event_id in open_by_fp.items():
            if fp not in active_fps:
                conn.execute("UPDATE alert_events SET resolved_at = ? WHERE id = ?", (now_iso, event_id))

        return new_alerts

    def _prune_host_locked(self, conn: sqlite3.Connection, host: str) -> None:
        row = conn.execute("SELECT value_json FROM app_settings WHERE key = 'retain_days'").fetchone()
        retain_days = int(json.loads(row["value_json"])) if row else self.default_retain_days
        if retain_days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=retain_days)).isoformat()
            conn.execute("DELETE FROM reports WHERE host = ? AND received_at < ?", (host, cutoff))
            conn.execute(
                "DELETE FROM alert_events WHERE host = ? AND resolved_at IS NOT NULL AND resolved_at < ?",
                (host, cutoff),
            )
        if self.max_per_host > 0:
            conn.execute(
                """
                DELETE FROM reports
                WHERE host = ? AND id NOT IN (
                    SELECT id FROM reports WHERE host = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (host, host, self.max_per_host),
            )

    # ------------------------------------------------------------ read queries
    def _stale_seconds(self) -> int:
        settings = self.get_settings()
        return int(settings.get("stale_seconds") or self.default_stale_seconds)

    def _host_status(self, last_seen: str, stale_seconds: int) -> tuple[str, int]:
        dt = parse_iso(last_seen)
        if not dt:
            return "unknown", -1
        age = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
        if age <= stale_seconds:
            return "online", age
        if age <= stale_seconds * 5:
            return "stale", age
        return "offline", age

    def list_hosts(self, include_sparkline: bool = False) -> list[dict]:
        stale_seconds = self._stale_seconds()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT host, os, first_seen, last_seen, report_timestamp,
                       cpu_pct, cpu_count, load_1m, uptime_h,
                       mem_used_mb, mem_total_mb, mem_used_pct,
                       gpu_count, gpu_util_avg, gpu_mem_used_mb, gpu_mem_total_mb, gpu_temp_max,
                       disk_max_pct, jupyterhub_service, online_count, users_count,
                       heavy_count, gpu_idle_count, alerts_count, note
                FROM hosts
                ORDER BY last_seen DESC
                """
            ).fetchall()

            counts = {
                r["host"]: r["cnt"]
                for r in conn.execute("SELECT host, COUNT(*) AS cnt FROM reports GROUP BY host").fetchall()
            }

            sparklines: dict[str, list[dict]] = {}
            if include_sparkline:
                for r in rows:
                    h = r["host"]
                    pts = conn.execute(
                        """
                        SELECT cpu_pct, mem_used_pct, gpu_util_avg
                        FROM reports WHERE host = ?
                        ORDER BY id DESC LIMIT 24
                        """,
                        (h,),
                    ).fetchall()
                    sparklines[h] = [dict(p) for p in reversed(pts)]

        hosts = []
        for r in rows:
            item = dict(r)
            status, age_s = self._host_status(item["last_seen"], stale_seconds)
            item["status"] = status
            item["age_seconds"] = age_s
            item["reports_count"] = counts.get(item["host"], 0)
            g_tot = float(item.get("gpu_mem_total_mb") or 0)
            g_used = float(item.get("gpu_mem_used_mb") or 0)
            item["gpu_mem_pct"] = round((g_used / g_tot) * 100, 1) if g_tot > 0 else 0.0
            if include_sparkline:
                item["sparkline"] = sparklines.get(item["host"], [])
            hosts.append(item)
        return hosts

    def get_fleet_summary(self) -> dict:
        hosts = self.list_hosts(include_sparkline=True)
        online = sum(1 for h in hosts if h["status"] == "online")
        stale = sum(1 for h in hosts if h["status"] == "stale")
        offline = sum(1 for h in hosts if h["status"] == "offline")
        total_cpus = sum(int(h.get("cpu_count") or 0) for h in hosts)
        total_mem_used = sum(float(h.get("mem_used_mb") or 0) for h in hosts)
        total_mem = sum(float(h.get("mem_total_mb") or 0) for h in hosts)
        total_gpus = sum(int(h.get("gpu_count") or 0) for h in hosts)
        total_vram_used = sum(float(h.get("gpu_mem_used_mb") or 0) for h in hosts)
        total_vram = sum(float(h.get("gpu_mem_total_mb") or 0) for h in hosts)
        total_online_sessions = sum(int(h.get("online_count") or 0) for h in hosts)
        total_alerts = sum(int(h.get("alerts_count") or 0) for h in hosts)
        avg_cpu = round(sum(float(h.get("cpu_pct") or 0) for h in hosts) / len(hosts), 1) if hosts else 0.0

        return {
            "totals": {
                "hosts": len(hosts),
                "online": online,
                "stale": stale,
                "offline": offline,
                "avg_cpu_pct": avg_cpu,
                "total_cpus": total_cpus,
                "mem_used_mb": round(total_mem_used, 1),
                "mem_total_mb": round(total_mem, 1),
                "mem_used_pct": round((total_mem_used / total_mem) * 100, 1) if total_mem > 0 else 0.0,
                "total_gpus": total_gpus,
                "vram_used_mb": round(total_vram_used, 1),
                "vram_total_mb": round(total_vram, 1),
                "vram_used_pct": round((total_vram_used / total_vram) * 100, 1) if total_vram > 0 else 0.0,
                "online_sessions": total_online_sessions,
                "active_alerts": total_alerts,
            },
            "hosts": hosts,
            "now": utc_now_iso(),
        }

    def get_latest(self, host: str, snapshot_id: int | None = None) -> dict | None:
        stale_seconds = self._stale_seconds()
        with self._connect() as conn:
            hrow = conn.execute(
                "SELECT host, first_seen, last_seen, latest_json, note FROM hosts WHERE host = ?",
                (host,),
            ).fetchone()
            if not hrow:
                return None
            cnt_row = conn.execute("SELECT COUNT(*) AS cnt FROM reports WHERE host = ?", (host,)).fetchone()
            acks = {
                r["fingerprint"]: dict(r)
                for r in conn.execute("SELECT * FROM alert_acks WHERE host = ?", (host,)).fetchall()
            }

            is_snapshot = False
            snap_received_at = hrow["last_seen"]
            raw_json = hrow["latest_json"]
            if snapshot_id is not None:
                srow = conn.execute(
                    "SELECT id, received_at, payload_json FROM reports WHERE host = ? AND id = ?",
                    (host, snapshot_id),
                ).fetchone()
                if not srow:
                    return None
                is_snapshot = True
                snap_received_at = srow["received_at"]
                raw_json = srow["payload_json"]

        status, age_s = self._host_status(hrow["last_seen"], stale_seconds)
        report = json.loads(raw_json)
        parsed_alerts = []
        for raw in report.get("alerts") or []:
            a = parse_alert(raw)
            ack = acks.get(a["fingerprint"])
            a["acked"] = bool(ack)
            a["ack_note"] = ack["note"] if ack else ""
            a["acked_at"] = ack["acked_at"] if ack else None
            parsed_alerts.append(a)

        return {
            "host": hrow["host"],
            "note": hrow["note"] or "",
            "status": status,
            "age_seconds": age_s,
            "first_seen": hrow["first_seen"],
            "last_seen": hrow["last_seen"],
            "snapshot_id": snapshot_id,
            "is_snapshot": is_snapshot,
            "snapshot_received_at": snap_received_at,
            "reports_count": cnt_row["cnt"] if cnt_row else 0,
            "parsed_alerts": parsed_alerts,
            "report": report,
        }

    def list_snapshots(self, host: str, limit: int = 60) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, received_at, cpu_pct, mem_used_pct,
                       gpu_util_avg, disk_max_pct, alerts_count
                FROM reports
                WHERE host = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (host, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_history(self, host: str, hours: float = 24.0, limit: int = 360) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, received_at, cpu_pct, load_1m,
                       mem_used_mb, mem_total_mb, mem_used_pct,
                       gpu_util_avg, gpu_mem_used_mb, gpu_mem_total_mb, gpu_temp_max,
                       disk_max_pct, online_count, heavy_count, alerts_count
                FROM reports
                WHERE host = ? AND received_at >= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (host, cutoff, limit),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    """
                    SELECT id, timestamp, received_at, cpu_pct, load_1m,
                           mem_used_mb, mem_total_mb, mem_used_pct,
                           gpu_util_avg, gpu_mem_used_mb, gpu_mem_total_mb, gpu_temp_max,
                           disk_max_pct, online_count, heavy_count, alerts_count
                    FROM reports
                    WHERE host = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (host, min(limit, 120)),
                ).fetchall()

        points = [dict(r) for r in reversed(rows)]
        for p in points:
            g_tot = float(p.get("gpu_mem_total_mb") or 0)
            g_used = float(p.get("gpu_mem_used_mb") or 0)
            p["gpu_mem_pct"] = round((g_used / g_tot) * 100, 1) if g_tot > 0 else 0.0
        return points

    def get_account_history(self, host: str, username: str, limit: int = 90) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, received_at, payload_json
                FROM reports
                WHERE host = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (host, limit),
            ).fetchall()

        points = []
        for r in reversed(rows):
            try:
                payload = json.loads(r["payload_json"])
            except json.JSONDecodeError:
                continue
            found = None
            for u in payload.get("usage_by_account") or []:
                if u.get("username") == username:
                    found = u
                    break
            points.append(
                {
                    "timestamp": r["timestamp"],
                    "received_at": r["received_at"],
                    "cpu_pct": float(found.get("cpu_pct") or 0) if found else 0.0,
                    "mem_mb": float(found.get("mem_mb") or 0) if found else 0.0,
                    "gpu_mem_mb": float(found.get("gpu_mem_mb") or 0) if found else 0.0,
                    "procs": int(found.get("procs") or 0) if found else 0,
                }
            )
        return points

    # ------------------------------------------------------------ alerts & acks
    def get_all_alerts(self) -> list[dict]:
        stale_seconds = self._stale_seconds()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT host, last_seen, latest_json FROM hosts ORDER BY alerts_count DESC, last_seen DESC"
            ).fetchall()
            ack_rows = conn.execute("SELECT * FROM alert_acks").fetchall()
            open_events = conn.execute(
                "SELECT host, fingerprint, opened_at, occurrences FROM alert_events WHERE resolved_at IS NULL"
            ).fetchall()

        acks = {(r["host"], r["fingerprint"]): dict(r) for r in ack_rows}
        event_meta = {(r["host"], r["fingerprint"]): dict(r) for r in open_events}

        results = []
        for row in rows:
            status, age_s = self._host_status(row["last_seen"], stale_seconds)
            report = json.loads(row["latest_json"])
            for raw in report.get("alerts") or []:
                parsed = parse_alert(raw)
                key = (row["host"], parsed["fingerprint"])
                ack = acks.get(key)
                ev = event_meta.get(key)
                parsed.update(
                    {
                        "host": row["host"],
                        "host_status": status,
                        "last_seen": row["last_seen"],
                        "age_seconds": age_s,
                        "acked": bool(ack),
                        "ack_note": ack["note"] if ack else "",
                        "acked_at": ack["acked_at"] if ack else None,
                        "opened_at": ev["opened_at"] if ev else row["last_seen"],
                        "occurrences": ev["occurrences"] if ev else 1,
                    }
                )
                results.append(parsed)

        sev_order = {"critical": 0, "warning": 1, "info": 2}
        results.sort(key=lambda a: (1 if a["acked"] else 0, sev_order.get(a["severity"], 9), a["host"], a["tag"]))
        return results

    def get_alert_events(self, host: str | None = None, limit: int = 120) -> list[dict]:
        with self._connect() as conn:
            if host:
                rows = conn.execute(
                    "SELECT * FROM alert_events WHERE host = ? ORDER BY id DESC LIMIT ?",
                    (host, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alert_events ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def set_alert_ack(self, host: str, fingerprint: str, acked: bool, note: str = "", actor: str = "admin") -> dict:
        now_iso = utc_now_iso()
        with self._lock, self._connect() as conn:
            if acked:
                conn.execute(
                    """
                    INSERT INTO alert_acks (host, fingerprint, acked_at, acked_by, note)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(host, fingerprint) DO UPDATE SET
                        acked_at = excluded.acked_at,
                        acked_by = excluded.acked_by,
                        note = excluded.note
                    """,
                    (host, fingerprint, now_iso, actor, note.strip()),
                )
            else:
                conn.execute(
                    "DELETE FROM alert_acks WHERE host = ? AND fingerprint = ?",
                    (host, fingerprint),
                )
        return {"host": host, "fingerprint": fingerprint, "acked": acked, "note": note.strip()}

    # ------------------------------------------------------------ host management & export
    def update_host_note(self, host: str, note: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE hosts SET note = ? WHERE host = ?", (note.strip(), host))
            return cur.rowcount > 0

    def delete_host(self, host: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM hosts WHERE host = ?", (host,))
            conn.execute("DELETE FROM reports WHERE host = ?", (host,))
            conn.execute("DELETE FROM alert_events WHERE host = ?", (host,))
            conn.execute("DELETE FROM alert_acks WHERE host = ?", (host,))
            return cur.rowcount > 0

    def export_csv(self, host: str, kind: str) -> str | None:
        latest = self.get_latest(host)
        if not latest:
            return None
        report = latest["report"]
        buf = io.StringIO()
        buf.write("\ufeff")  # UTF-8 BOM for Excel
        writer = csv.writer(buf)

        if kind == "accounts":
            writer.writerow(
                [
                    "host",
                    "timestamp",
                    "username",
                    "cpu_pct",
                    "mem_mb",
                    "gpu_mem_mb",
                    "gpu_idle_mb",
                    "procs",
                    "zombies",
                    "oldest_proc_age_h",
                    "heavy",
                    "heavy_reasons",
                ]
            )
            for u in report.get("usage_by_account") or []:
                writer.writerow(
                    [
                        host,
                        report.get("timestamp", ""),
                        u.get("username", ""),
                        u.get("cpu_pct", 0),
                        u.get("mem_mb", 0),
                        u.get("gpu_mem_mb", 0),
                        u.get("gpu_idle_mb", 0),
                        u.get("procs", 0),
                        u.get("zombies", 0),
                        u.get("oldest_proc_age_h", 0),
                        u.get("heavy", False),
                        "; ".join(u.get("heavy_reasons") or []),
                    ]
                )
            return buf.getvalue()

        if kind == "users":
            writer.writerow(["host", "timestamp", "username", "home", "days_inactive", "disk_mb", "last_login"])
            for u in report.get("users") or []:
                writer.writerow(
                    [
                        host,
                        report.get("timestamp", ""),
                        u.get("username", ""),
                        u.get("home", ""),
                        "" if u.get("days_inactive") is None else u.get("days_inactive"),
                        "" if u.get("disk_mb") is None else u.get("disk_mb"),
                        u.get("last_login") or "Never logged in",
                    ]
                )
            return buf.getvalue()

        return None

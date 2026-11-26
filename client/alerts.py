"""Alert synthesis rules matching server dashboard expectations."""
from __future__ import annotations

from .config import ClientConfig


def build_alerts(report: dict, cfg: ClientConfig) -> list[str]:
    alerts: list[str] = []

    for u in report.get("usage_by_account") or []:
        if u.get("heavy"):
            alerts.append(f"[HEAVY] {u['username']}: " + "; ".join(u.get("heavy_reasons") or []))
        if (u.get("gpu_idle_mb") or 0) > 0:
            alerts.append(
                f"[GPU-IDLE] {u['username']} giu {u['gpu_idle_mb']}MB VRAM nhung "
                f"CPU ~0 (PID {u.get('gpu_idle_pids') or []}) - co the da bo quen"
            )
        if (u.get("zombies") or 0) >= cfg.zombie_threshold:
            alerts.append(f"[ZOMBIE] {u['username']} co {u['zombies']} tien trinh zombie")

    for usr in report.get("users") or []:
        d = usr.get("days_inactive")
        disk = usr.get("disk_mb")
        if disk is not None and disk >= cfg.disk_threshold_gb * 1024:
            alerts.append(f"[DISK] {usr['username']} dung {disk / 1024:.1f}GB")
        if d is not None and d >= cfg.inactive_days and disk is not None and disk > 1024:
            alerts.append(
                f"[INACTIVE] {usr['username']} {d} ngay khong dang nhap, van chiem {disk / 1024:.1f}GB"
            )

    sys_info = report.get("system") or {}
    for dk in sys_info.get("disks") or []:
        if (dk.get("used_pct") or 0) >= 90:
            alerts.append(f"[DISK-FULL] {dk['mount']} dung {dk['used_pct']}%")

    svc = sys_info.get("jupyterhub_service") or "unknown"
    if svc not in ("active", "unknown"):
        alerts.append(f"[SERVICE] jupyterhub: {svc}")

    for g in report.get("gpus") or []:
        if g.get("temp_c") is not None and g["temp_c"] >= 85:
            alerts.append(f"[GPU-TEMP] GPU{g['index']} {g['temp_c']}C")

    xids = report.get("gpu_xid_errors") or []
    if xids:
        alerts.append(f"[GPU-XID] {len(xids)} loi Xid gan day trong dmesg")

    for h in report.get("jupyterhub") or []:
        if h.get("server_running") and (h.get("idle_hours") or 0) >= cfg.hub_idle_hours:
            alerts.append(f"[HUB-IDLE] {h['name']} server chay nhung idle {h['idle_hours']}h")

    return alerts


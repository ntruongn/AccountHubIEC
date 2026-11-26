"""Non-blocking Webhook dispatcher for Slack, Discord, or generic HTTP endpoints."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

SEV_RANK = {"info": 1, "warning": 2, "critical": 3}


def _build_payload(url: str, host: str, alerts: list[dict]) -> dict:
    lines = [f"[{a['severity'].upper()}][{a['tag']}] {a['message']}" for a in alerts]
    summary = f"AccountHubIEC Alert on `{host}` ({len(alerts)} new):\n" + "\n".join(f"- {l}" for l in lines)
    lower = url.lower()
    if "discord.com/api/webhooks" in lower or "discordapp.com/api/webhooks" in lower:
        return {"content": summary[:1950]}
    if "hooks.slack.com" in lower:
        return {"text": summary}
    return {
        "source": "AccountHubIEC Monitor",
        "host": host,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "text": summary,
        "alerts": alerts,
    }


def send_webhook_sync(url: str, host: str, alerts: list[dict], timeout: int = 8) -> tuple[bool, str]:
    if not url or not url.startswith(("http://", "https://")):
        return False, "Webhook URL must start with http:// or https://"
    payload = _build_payload(url, host, alerts)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "AccountHubIEC-Monitor/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def dispatch_alerts_async(settings: dict, host: str, new_alerts: list[dict]) -> None:
    if not settings.get("webhook_enabled") or not settings.get("webhook_url") or not new_alerts:
        return
    min_sev = str(settings.get("webhook_min_severity") or "warning").lower()
    min_rank = SEV_RANK.get(min_sev, 2)
    filtered = [a for a in new_alerts if SEV_RANK.get(a.get("severity", "info"), 1) >= min_rank]
    if not filtered:
        return

    url = str(settings["webhook_url"]).strip()

    def _worker() -> None:
        ok, detail = send_webhook_sync(url, host, filtered)
        if not ok:
            sys.stderr.write(f"[WARN] Webhook delivery failed for host={host}: {detail}\n")

    threading.Thread(target=_worker, daemon=True).start()

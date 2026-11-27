"""Alert-to-owner linking, Vietnamese warning/request email templates, SMTP delivery, and anti-spam cooldown log."""
from __future__ import annotations

import html
import re
import smtplib
import sqlite3
import ssl
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

SEV_RANK = {"info": 1, "warning": 2, "critical": 3}

USER_ALERT_TAGS = {"HEAVY", "GPU-IDLE", "ZOMBIE", "DISK", "INACTIVE", "HUB-IDLE"}

ACTION_GUIDANCE = {
    "GPU-IDLE": (
        "Yêu cầu kiểm tra ngay các tiến trình đang giữ bộ nhớ GPU (VRAM) nhưng không tính toán (CPU ~0%). "
        "Nếu thí nghiệm đã xong hoặc notebook bị treo, vui lòng Shutdown Kernel trên JupyterHub hoặc dừng các PID liên quan "
        "để giải phóng VRAM cho các nhóm nghiên cứu khác."
    ),
    "HEAVY": (
        "Tài khoản của bạn/nhóm đang sử dụng tài nguyên (CPU / RAM / GPU) vượt ngưỡng cảnh báo của cụm máy dùng chung. "
        "Vui lòng kiểm tra tiến trình, cân nhắc giảm số worker/batch size hoặc phản hồi email này nếu bạn đang chạy thí nghiệm hợp lệ cần duy trì."
    ),
    "DISK": (
        "Dung lượng thư mục /home của tài khoản đã vượt ngưỡng cảnh báo. "
        "Vui lòng dọn dẹp các file checkpoint cũ, dataset tạm, thư mục cache (.cache/pip, .cache/huggingface, conda pkgs) "
        "để tránh làm đầy phân vùng hệ thống chung."
    ),
    "INACTIVE": (
        "Tài khoản đã lâu không ghi nhận đăng nhập nhưng vẫn đang chiếm dung lượng lưu trữ lớn trên máy chủ. "
        "Vui lòng phản hồi xác nhận nếu nhóm vẫn tiếp tục sử dụng, hoặc sao lưu dữ liệu cá nhân trước khi quản trị viên tiến hành dọn dẹp / thu hồi."
    ),
    "ZOMBIE": (
        "Phát hiện nhiều tiến trình zombie thuộc tài khoản của bạn. "
        "Vui lòng kiểm tra và khởi động lại notebook kernel hoặc tiến trình cha để tránh chiếm bảng tiến trình hệ thống."
    ),
    "HUB-IDLE": (
        "Notebook server JupyterHub của bạn đang chạy nhưng không có tương tác trong thời gian dài. "
        "Vui lòng truy cập Hub Control Panel và chọn 'Stop My Server' khi không làm việc để tiết kiệm RAM/GPU cho cụm máy."
    ),
    "DISK-FULL": (
        "Cảnh báo quản trị hệ thống: phân vùng ổ đĩa đã đạt mức nguy hiểm (>= 90%). "
        "Cần kiểm tra các thư mục /home, /var/log, Docker/conda cache và giải phóng dung lượng khẩn cấp."
    ),
    "GPU-TEMP": (
        "Cảnh báo phần cứng GPU: nhiệt độ vượt ngưỡng an toàn (>= 85°C). "
        "Cần kiểm tra quạt tản nhiệt, điều hòa phòng máy và tải compute hiện tại."
    ),
    "GPU-XID": (
        "Phát hiện lỗi NVRM Xid từ driver/phần cứng GPU trong kernel log (dmesg). "
        "Cần kiểm tra trạng thái nvidia-smi và lên lịch kiểm tra phần cứng/driver."
    ),
    "SERVICE": (
        "Dịch vụ JupyterHub trên máy chủ không ở trạng thái active. "
        "Cần kiểm tra systemctl status jupyterhub và journalctl -u jupyterhub."
    ),
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_alert_username(tag: str, message: str) -> str | None:
    """Extract target username from a parsed alert tag & message."""
    t = (tag or "").upper()
    if t not in USER_ALERT_TAGS:
        return None
    msg = (message or "").strip()
    if not msg:
        return None
    token = msg.split()[0].rstrip(":,;.")
    if not token:
        return None
    return token


def link_alert_owner(alert: dict, owner_map: dict[str, dict]) -> dict:
    """Attach `target_username` and matched `owner` (from AccountRegistry) to an alert dict."""
    tag = str(alert.get("tag") or "").upper()
    msg = str(alert.get("message") or "")
    target = extract_alert_username(tag, msg)
    owner = None
    if target and owner_map:
        owner = owner_map.get(target)
        if owner is None and tag == "HUB-IDLE" and not target.startswith("jupyter-"):
            owner = owner_map.get("jupyter-" + target)
            if owner:
                target = "jupyter-" + target

    emails: list[str] = []
    if owner:
        for m in owner.get("members") or []:
            em = (m.get("email") or "").strip()
            if em and em not in emails:
                emails.append(em)

    enriched = dict(alert)
    enriched["target_username"] = target
    if owner:
        enriched["owner"] = {
            "id": owner.get("id"),
            "username": owner.get("username"),
            "account_type": owner.get("account_type"),
            "group_name": owner.get("group_name") or "",
            "advisor": owner.get("advisor") or "",
            "status": owner.get("status") or "",
            "expires_at": owner.get("expires_at") or "",
            "members": owner.get("members") or [],
            "emails": emails,
        }
    else:
        enriched["owner"] = None
    return enriched


def draft_alert_email(
    host: str,
    alert: dict,
    owner: dict | None = None,
    custom_request: str = "",
) -> dict:
    """Generate structured Vietnamese warning + action-request email (subject, text, html, recipients)."""
    tag = str(alert.get("tag") or "INFO").upper()
    sev = str(alert.get("severity") or "info").upper()
    msg = str(alert.get("message") or alert.get("raw") or "")
    target_user = alert.get("target_username") or (owner.get("username") if owner else "")

    members = (owner.get("members") or []) if owner else []
    emails = [m["email"].strip() for m in members if (m.get("email") or "").strip()]
    names = [m["full_name"].strip() for m in members if (m.get("full_name") or "").strip()]
    group_name = (owner.get("group_name") or "").strip() if owner else ""
    advisor = (owner.get("advisor") or "").strip() if owner else ""

    greeting_target = ", ".join(names) if names else (group_name or target_user or "Quản trị viên")
    guidance = custom_request.strip() or ACTION_GUIDANCE.get(
        tag,
        "Vui lòng kiểm tra tình trạng sử dụng tài nguyên của tài khoản và phản hồi lại cho quản trị hệ thống.",
    )

    subject_target = f"tài khoản {target_user} " if target_user else ""
    subject = f"[AccountHubIEC][{tag}] Cảnh báo & yêu cầu xử lý {subject_target}trên máy chủ {host}"

    lines = [
        f"Kính gửi {greeting_target},",
        "",
        f"Hệ thống giám sát tài nguyên AccountHubIEC vừa ghi nhận một cảnh báo liên quan đến cụm máy chủ `{host}`:",
        "",
        f"• Máy chủ       : {host}",
    ]
    if target_user:
        lines.append(f"• Tài khoản     : {target_user}")
    if group_name:
        lines.append(f"• Nhóm / Đề tài : {group_name}")
    if advisor:
        lines.append(f"• Phụ trách/GVHD: {advisor}")
    lines.extend(
        [
            f"• Loại cảnh báo : [{tag}] (Mức độ: {sev})",
            f"• Chi tiết      : {msg}",
            "",
            "YÊU CẦU XỬ LÝ:",
            guidance,
            "",
            "Nếu đây là tác vụ nghiên cứu đang chạy hợp lệ hoặc bạn cần hỗ trợ, vui lòng phản hồi lại email này cho quản trị viên.",
            "",
            "Trân trọng,",
            "Hệ thống Quản lý & Giám sát AccountHubIEC",
        ]
    )
    body_text = "\n".join(lines)

    body_html = _render_html_email(
        title=f"Cảnh báo [{tag}] trên máy chủ {host}",
        severity=sev.lower(),
        greeting=f"Kính gửi {greeting_target},",
        meta_rows=[
            ("Máy chủ", host),
            *([("Tài khoản", target_user)] if target_user else []),
            *([("Nhóm / Đề tài", group_name)] if group_name else []),
            *([("Phụ trách / GVHD", advisor)] if advisor else []),
            ("Loại cảnh báo", f"[{tag}] — {sev}"),
            ("Chi tiết", msg),
        ],
        request_text=guidance,
    )

    return {
        "to": emails,
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
    }


def draft_account_email(account: dict, kind: str = "general", custom_request: str = "") -> dict:
    """Generate a Vietnamese email draft directly for a managed account (expiry, cleanup, onboarding, general)."""
    host = account.get("host") or ""
    username = account.get("username") or ""
    group_name = account.get("group_name") or ""
    advisor = account.get("advisor") or ""
    expires_at = account.get("expires_at") or ""
    members = account.get("members") or []
    emails = [m["email"].strip() for m in members if (m.get("email") or "").strip()]
    names = [m["full_name"].strip() for m in members if (m.get("full_name") or "").strip()]
    greeting_target = ", ".join(names) if names else (group_name or username)

    if kind == "expiry":
        subject = f"[AccountHubIEC] Thông báo gia hạn tài khoản {username} trên máy chủ {host}"
        request = custom_request.strip() or (
            f"Tài khoản {username} dự kiến hết hạn vào ngày {expires_at or '(chưa cập nhật)'}. "
            "Nếu nhóm vẫn tiếp tục nghiên cứu trên máy chủ này, vui lòng phản hồi đề nghị gia hạn; "
            "ngược lại vui lòng sao lưu dữ liệu cá nhân trước khi hệ thống khóa và thu hồi tài khoản."
        )
        sev = "warning"
        detail = f"Hạn sử dụng hiện tại: {expires_at or 'Không xác định'}"
    elif kind == "cleanup":
        t = account.get("telemetry") or {}
        disk_mb = t.get("disk_mb")
        disk_str = f"{disk_mb / 1024:.1f} GB" if disk_mb else "chưa đo"
        subject = f"[AccountHubIEC] Yêu cầu dọn dẹp dung lượng tài khoản {username} trên {host}"
        request = custom_request.strip() or (
            f"Dung lượng thư mục /home/{username} hiện khoảng {disk_str}. "
            "Vui lòng xóa các file checkpoint, log, dataset tạm và cache không cần thiết để đảm bảo không gian lưu trữ chung cho lab."
        )
        sev = "warning"
        detail = f"Dung lượng /home: {disk_str}"
    else:
        subject = f"[AccountHubIEC] Thông báo liên quan đến tài khoản {username} trên máy chủ {host}"
        request = custom_request.strip() or (
            "Vui lòng kiểm tra trạng thái sử dụng tài khoản và phối hợp cùng quản trị viên hệ thống."
        )
        sev = "info"
        detail = f"Trạng thái tài khoản: {account.get('status', 'active')}"

    lines = [
        f"Kính gửi {greeting_target},",
        "",
        f"Thông tin tài khoản nghiên cứu trên cụm máy `{host}`:",
        f"• Tài khoản     : {username}",
        *([f"• Nhóm / Đề tài : {group_name}"] if group_name else []),
        *([f"• Phụ trách/GVHD: {advisor}"] if advisor else []),
        f"• Ghi nhận      : {detail}",
        "",
        "NỘI DUNG / YÊU CẦU:",
        request,
        "",
        "Trân trọng,",
        "Hệ thống Quản lý & Giám sát AccountHubIEC",
    ]
    body_text = "\n".join(lines)
    body_html = _render_html_email(
        title=subject,
        severity=sev,
        greeting=f"Kính gửi {greeting_target},",
        meta_rows=[
            ("Máy chủ", host),
            ("Tài khoản", username),
            *([("Nhóm / Đề tài", group_name)] if group_name else []),
            *([("Phụ trách / GVHD", advisor)] if advisor else []),
            ("Thông tin", detail),
        ],
        request_text=request,
    )
    return {"to": emails, "subject": subject, "body_text": body_text, "body_html": body_html}


def _render_html_email(
    title: str,
    severity: str,
    greeting: str,
    meta_rows: list[tuple[str, str]],
    request_text: str,
) -> str:
    accent = "#dc2626" if severity == "critical" else ("#d97706" if severity == "warning" else "#0284c7")
    rows_html = "".join(
        f'<tr><td style="padding:6px 10px;color:#475569;font-weight:600;white-space:nowrap;border-bottom:1px solid #e2e8f0">{html.escape(k)}</td>'
        f'<td style="padding:6px 10px;color:#0f172a;border-bottom:1px solid #e2e8f0">{html.escape(v)}</td></tr>'
        for k, v in meta_rows
    )
    req_html = "<br>".join(html.escape(line) for line in request_text.splitlines())
    return f"""<!DOCTYPE html>
<html lang="vi"><body style="margin:0;padding:20px;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#0f172a">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #cbd5e1">
    <div style="background:{accent};color:#ffffff;padding:14px 20px;font-size:16px;font-weight:700">{html.escape(title)}</div>
    <div style="padding:20px;font-size:14px;line-height:1.6">
      <p style="margin-top:0">{html.escape(greeting)}</p>
      <table style="width:100%;border-collapse:collapse;margin:14px 0;background:#f8fafc;border:1px solid #e2e8f0">{rows_html}</table>
      <div style="padding:12px 14px;background:#fffbeb;border-left:4px solid {accent};border-radius:4px;margin:16px 0">
        <div style="font-weight:700;margin-bottom:4px;color:#0f172a">Yêu cầu xử lý:</div>
        <div>{req_html}</div>
      </div>
      <p style="color:#64748b;font-size:12px;margin-bottom:0">Email này được gửi từ hệ thống quản lý &amp; giám sát AccountHubIEC.</p>
    </div>
  </div>
</body></html>"""


def _parse_email_list(raw) -> list[str]:
    if isinstance(raw, list):
        items = raw
    else:
        items = re.split(r"[,;\s]+", str(raw or ""))
    out: list[str] = []
    for item in items:
        s = str(item or "").strip()
        if s and "@" in s and s not in out:
            out.append(s)
    return out


class EmailService:
    def __init__(self, store, smtp_config):
        self.store = store
        self.smtp = smtp_config
        self._init_table()

    def _init_table(self) -> None:
        with self.store._lock, self.store._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS email_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    alert_tag TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'manual',
                    recipients TEXT NOT NULL,
                    cc TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_email_logs_cooldown
                    ON email_logs(host, fingerprint, sent_at DESC);
                """
            )

    @property
    def configured(self) -> bool:
        return bool(self.smtp.enabled and self.smtp.host)

    def public_status(self) -> dict:
        return {
            "smtp_configured": self.configured,
            "smtp_host": self.smtp.host,
            "smtp_port": self.smtp.port,
            "smtp_from": self.smtp.from_email or self.smtp.username,
            "smtp_tls": self.smtp.use_tls,
            "smtp_ssl": self.smtp.use_ssl,
            "default_admin_emails": list(self.smtp.admin_emails or []),
        }

    def list_logs(self, limit: int = 80) -> list[dict]:
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM email_logs ORDER BY id DESC LIMIT ?", (max(1, min(300, limit)),)
            ).fetchall()
        return [dict(r) for r in rows]

    def _record_log(
        self,
        *,
        host: str,
        username: str,
        alert_tag: str,
        fingerprint: str,
        mode: str,
        to_emails: list[str],
        cc_emails: list[str],
        subject: str,
        ok: bool,
        error: str = "",
    ) -> None:
        with self.store._lock, self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO email_logs (
                    host, username, alert_tag, fingerprint, mode,
                    recipients, cc, subject, status, error, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    host,
                    username,
                    alert_tag,
                    fingerprint,
                    mode,
                    ", ".join(to_emails),
                    ", ".join(cc_emails),
                    subject,
                    "sent" if ok else "failed",
                    error[:500],
                    _utc_now_iso(),
                ),
            )

    def _in_cooldown(self, host: str, fingerprint: str, cooldown_minutes: int) -> bool:
        if not fingerprint or cooldown_minutes <= 0:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).isoformat()
        with self.store._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM email_logs
                WHERE host = ? AND fingerprint = ? AND status = 'sent' AND sent_at >= ?
                LIMIT 1
                """,
                (host, fingerprint, cutoff),
            ).fetchone()
        return row is not None

    def send_email_sync(
        self,
        to_emails,
        subject: str,
        body_text: str,
        body_html: str | None = None,
        cc_emails=None,
        *,
        host: str = "",
        username: str = "",
        alert_tag: str = "",
        fingerprint: str = "",
        mode: str = "manual",
        registry=None,
        account_id: int | None = None,
    ) -> tuple[bool, str]:
        to_list = _parse_email_list(to_emails)
        cc_list = [e for e in _parse_email_list(cc_emails) if e not in to_list]
        if not to_list:
            return False, "Không có địa chỉ email người nhận hợp lệ"
        if not self.configured:
            msg = "Chưa cấu hình SMTP trong server_config.json (smtp.enabled=true, smtp.host)"
            self._record_log(
                host=host, username=username, alert_tag=alert_tag, fingerprint=fingerprint,
                mode=mode, to_emails=to_list, cc_emails=cc_list, subject=subject, ok=False, error=msg,
            )
            return False, msg

        sender = self.smtp.from_email or self.smtp.username or f"accounthub@{self.smtp.host}"
        msg_obj = EmailMessage()
        msg_obj["Subject"] = subject
        msg_obj["From"] = sender
        msg_obj["To"] = ", ".join(to_list)
        if cc_list:
            msg_obj["Cc"] = ", ".join(cc_list)
        msg_obj.set_content(body_text, charset="utf-8")
        if body_html:
            msg_obj.add_alternative(body_html, subtype="html", charset="utf-8")

        try:
            timeout = max(5, int(self.smtp.timeout or 15))
            tls_ctx = ssl.create_default_context()
            tls_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            if self.smtp.use_ssl:
                with smtplib.SMTP_SSL(self.smtp.host, int(self.smtp.port or 465), timeout=timeout, context=tls_ctx) as srv:
                    if self.smtp.username:
                        srv.login(self.smtp.username, self.smtp.password)
                    srv.send_message(msg_obj, to_addrs=to_list + cc_list)
            else:
                with smtplib.SMTP(self.smtp.host, int(self.smtp.port or 587), timeout=timeout) as srv:
                    srv.ehlo()
                    if self.smtp.use_tls:
                        srv.starttls(context=tls_ctx)
                        srv.ehlo()
                    if self.smtp.username:
                        srv.login(self.smtp.username, self.smtp.password)
                    srv.send_message(msg_obj, to_addrs=to_list + cc_list)
            ok, detail = True, f"Đã gửi tới {', '.join(to_list)}"
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc)

        self._record_log(
            host=host, username=username, alert_tag=alert_tag, fingerprint=fingerprint,
            mode=mode, to_emails=to_list, cc_emails=cc_list, subject=subject,
            ok=ok, error="" if ok else detail,
        )

        # Also record into Managed Account Audit Log if linked
        if ok and registry is not None and account_id:
            try:
                registry.apply_lifecycle_action(
                    int(account_id),
                    {
                        "action_type": "email_sent",
                        "summary": f"Đã gửi email ({mode}) tới {', '.join(to_list)}: {subject}",
                    },
                )
            except Exception:  # noqa: BLE001
                pass

        return ok, detail

    def dispatch_alert_emails_async(
        self,
        host: str,
        parsed_alerts: list[dict],
        owner_map: dict[str, dict],
        settings: dict,
        registry=None,
    ) -> None:
        """Background auto-email dispatcher respecting auto_send, severity threshold, and cooldown."""
        if not self.configured or not parsed_alerts:
            return
        auto_enabled = bool(settings.get("email_auto_enabled", self.smtp.auto_send))
        if not auto_enabled:
            return

        min_sev = str(settings.get("email_min_severity") or self.smtp.auto_min_severity or "warning").lower()
        min_rank = SEV_RANK.get(min_sev, 2)
        cooldown = int(settings.get("email_cooldown_minutes", self.smtp.cooldown_minutes))
        cc_admin = bool(settings.get("email_cc_admin", self.smtp.cc_admin))
        admin_emails = _parse_email_list(settings.get("email_admin_emails") or self.smtp.admin_emails)

        def _worker() -> None:
            for raw_alert in parsed_alerts:
                if SEV_RANK.get(str(raw_alert.get("severity") or "info").lower(), 1) < min_rank:
                    continue
                fp = str(raw_alert.get("fingerprint") or "")
                if self._in_cooldown(host, fp, cooldown):
                    continue

                linked = link_alert_owner(raw_alert, owner_map)
                owner = linked.get("owner")
                draft = draft_alert_email(host, linked, owner=owner)

                to_list = draft["to"]
                cc_list: list[str] = []
                if to_list:
                    if cc_admin:
                        cc_list = [e for e in admin_emails if e not in to_list]
                else:
                    # System-wide alert or user has no email -> route to admin_emails
                    to_list = admin_emails
                if not to_list:
                    continue

                ok, detail = self.send_email_sync(
                    to_list,
                    draft["subject"],
                    draft["body_text"],
                    draft["body_html"],
                    cc_emails=cc_list,
                    host=host,
                    username=linked.get("target_username") or "",
                    alert_tag=linked.get("tag") or "",
                    fingerprint=fp,
                    mode="auto",
                    registry=registry,
                    account_id=owner.get("id") if owner else None,
                )
                if not ok:
                    sys.stderr.write(f"[WARN] Auto-email failed for {host}/{linked.get('tag')}: {detail}\n")

        threading.Thread(target=_worker, daemon=True).start()

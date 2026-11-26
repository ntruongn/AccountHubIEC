"""Threaded HTTP/HTTPS server handling REST APIs, SSE real-time stream, CSV export, and SPA static files."""
from __future__ import annotations

import json
import mimetypes
import queue
import ssl
import sys
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .auth import AuthManager
from .config import STATIC_DIR, AppConfig
from .sse import EventBroker
from .store import MonitorStore, utc_now_iso
from .webhooks import dispatch_alerts_async, send_webhook_sync

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")


def build_server_ssl_context(config: AppConfig) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if hasattr(ssl, "OP_NO_COMPRESSION"):
        ctx.options |= ssl.OP_NO_COMPRESSION
    ctx.load_cert_chain(certfile=config.tls_cert, keyfile=config.tls_key)
    if config.mtls_enabled:
        ctx.load_verify_locations(cafile=config.tls_ca_cert)
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


class MonitorWebServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, config: AppConfig, store: MonitorStore, auth: AuthManager, broker: EventBroker):
        super().__init__((config.host, config.port), MonitorHandler)
        self.config = config
        self.store = store
        self.auth = auth
        self.broker = broker
        self.started_at = utc_now_iso()

        if config.tls_enabled:
            ssl_ctx = build_server_ssl_context(config)
            self.socket = ssl_ctx.wrap_socket(self.socket, server_side=True)


class MonitorHandler(BaseHTTPRequestHandler):
    server: MonitorWebServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        ts = datetime.now().strftime("%H:%M:%S")
        sys.stderr.write(f"[{ts}] {self.address_string()} - {format % args}\n")

    # ------------------------------------------------------------ helpers
    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = HTTPStatus.OK,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.server.config.tls_enabled:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        data: dict | list,
        status: int = HTTPStatus.OK,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers = {"Cache-Control": "no-store"}
        if extra_headers:
            headers.update(extra_headers)
        self._send_bytes(body, "application/json; charset=utf-8", status=status, extra_headers=headers)

    def _send_file(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            static_root = STATIC_DIR.resolve()
            if not str(resolved).startswith(str(static_root)) or not resolved.is_file():
                self._send_json({"error": "File not found"}, HTTPStatus.NOT_FOUND)
                return
            content = resolved.read_bytes()
        except OSError:
            self._send_json({"error": "File not found"}, HTTPStatus.NOT_FOUND)
            return

        ctype, _ = mimetypes.guess_type(str(resolved))
        if not ctype:
            ctype = "application/octet-stream"
        if ctype.startswith(("text/", "application/javascript")):
            ctype += "; charset=utf-8"

        self._send_bytes(content, ctype, HTTPStatus.OK, {"Cache-Control": "no-cache"})

    def _read_raw_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "Invalid Content-Length"}, HTTPStatus.BAD_REQUEST)
            return None
        if length <= 0 or length > self.server.config.max_body_bytes:
            status = (
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                if length > self.server.config.max_body_bytes
                else HTTPStatus.BAD_REQUEST
            )
            self._send_json({"error": "Request body empty or too large"}, status)
            return None
        return self.rfile.read(length)

    def _parse_json_bytes(self, raw: bytes) -> dict | None:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json({"error": f"Invalid JSON: {exc}"}, HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(data, dict):
            self._send_json({"error": "JSON payload must be an object"}, HTTPStatus.BAD_REQUEST)
            return None
        return data

    def _read_json_body(self) -> dict | None:
        raw = self._read_raw_body()
        if raw is None:
            return None
        return self._parse_json_bytes(raw)

    def _require_ui_auth(self) -> dict | None:
        session = self.server.auth.verify_session_cookie(self.headers.get("Cookie", ""))
        if session is None:
            self._send_json({"error": "Authentication required", "auth_required": True}, HTTPStatus.UNAUTHORIZED)
            return None
        return session

    def _require_write_auth(self) -> bool:
        """Allow write if UI session is valid (when admin password enabled) OR bot token matches."""
        if self.server.auth.ui_auth_enabled:
            if self.server.auth.verify_session_cookie(self.headers.get("Cookie", "")) is not None:
                return True
            self._send_json({"error": "Admin login required"}, HTTPStatus.UNAUTHORIZED)
            return False
        if self.server.auth.bot_auth_enabled:
            if self.server.auth.verify_bot_token(self.headers.get("Authorization", "")):
                return True
            self._send_json({"error": "Bearer token required"}, HTTPStatus.UNAUTHORIZED)
            return False
        return True

    # ------------------------------------------------------------ SSE stream
    def _handle_sse(self) -> None:
        if self._require_ui_auth() is None:
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        sub = self.server.broker.subscribe()
        try:
            hello = json.dumps({"ok": True, "now": utc_now_iso()})
            self.wfile.write(f"event: connected\ndata: {hello}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    event_name, payload = sub.get(timeout=20.0)
                    chunk = f"event: {event_name}\ndata: {payload}\n\n".encode("utf-8")
                except queue.Empty:
                    chunk = b": keepalive\n\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.broker.unsubscribe(sub)

    # ------------------------------------------------------------ GET
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "started_at": self.server.started_at,
                    "now": utc_now_iso(),
                    "tls_enabled": self.server.config.tls_enabled,
                    "mtls_enabled": self.server.config.mtls_enabled,
                    "hmac_required": self.server.auth.require_hmac,
                    "bot_auth_enabled": self.server.auth.bot_auth_enabled,
                    "ui_auth_enabled": self.server.auth.ui_auth_enabled,
                    "sse_clients": self.server.broker.subscriber_count(),
                }
            )
            return

        if path == "/api/auth/me":
            session = self.server.auth.verify_session_cookie(self.headers.get("Cookie", ""))
            self._send_json(
                {
                    "ui_auth_enabled": self.server.auth.ui_auth_enabled,
                    "bot_auth_enabled": self.server.auth.bot_auth_enabled,
                    "tls_enabled": self.server.config.tls_enabled,
                    "authenticated": session is not None,
                    "user": session.get("u") if session else None,
                }
            )
            return

        if path == "/api/stream":
            self._handle_sse()
            return

        if path.startswith("/api/"):
            if self._require_ui_auth() is None:
                return

            if path == "/api/fleet":
                self._send_json(self.server.store.get_fleet_summary())
                return

            if path == "/api/hosts":
                hosts = self.server.store.list_hosts(include_sparkline=qs.get("sparkline", ["0"])[0] == "1")
                self._send_json({"hosts": hosts, "now": utc_now_iso()})
                return

            if path == "/api/alerts":
                self._send_json({"alerts": self.server.store.get_all_alerts(), "now": utc_now_iso()})
                return

            if path == "/api/alerts/events":
                host_filter = qs.get("host", [None])[0]
                limit = max(10, min(500, int(qs.get("limit", ["120"])[0])))
                events = self.server.store.get_alert_events(host=host_filter, limit=limit)
                self._send_json({"events": events})
                return

            if path == "/api/settings":
                self._send_json(
                    {
                        "settings": self.server.store.get_settings(),
                        "bot_auth_enabled": self.server.auth.bot_auth_enabled,
                        "ui_auth_enabled": self.server.auth.ui_auth_enabled,
                        "tls_enabled": self.server.config.tls_enabled,
                        "mtls_enabled": self.server.config.mtls_enabled,
                        "hmac_required": self.server.auth.require_hmac,
                    }
                )
                return

            if path.startswith("/api/hosts/"):
                parts = [unquote(p) for p in path[len("/api/hosts/") :].strip("/").split("/")]
                host = parts[0]

                if len(parts) == 2 and parts[1] == "latest":
                    snap_raw = qs.get("snapshot_id", [None])[0]
                    snap_id = int(snap_raw) if snap_raw and snap_raw.isdigit() else None
                    data = self.server.store.get_latest(host, snapshot_id=snap_id)
                    if not data:
                        self._send_json({"error": "Host or snapshot not found"}, HTTPStatus.NOT_FOUND)
                        return
                    self._send_json(data)
                    return

                if len(parts) == 2 and parts[1] == "snapshots":
                    limit = max(5, min(200, int(qs.get("limit", ["50"])[0])))
                    self._send_json({"host": host, "snapshots": self.server.store.list_snapshots(host, limit=limit)})
                    return

                if len(parts) == 2 and parts[1] == "history":
                    try:
                        hours = max(0.1, min(24 * 60, float(qs.get("hours", ["24"])[0])))
                        limit = max(10, min(2000, int(qs.get("limit", ["360"])[0])))
                    except ValueError:
                        self._send_json({"error": "Invalid query params"}, HTTPStatus.BAD_REQUEST)
                        return
                    points = self.server.store.get_history(host, hours=hours, limit=limit)
                    self._send_json({"host": host, "hours": hours, "points": points})
                    return

                if len(parts) == 4 and parts[1] == "accounts" and parts[3] == "history":
                    username = parts[2]
                    limit = max(10, min(300, int(qs.get("limit", ["90"])[0])))
                    points = self.server.store.get_account_history(host, username, limit=limit)
                    self._send_json({"host": host, "username": username, "points": points})
                    return

                if len(parts) == 3 and parts[1] == "export":
                    kind = parts[2]
                    if kind not in ("accounts", "users"):
                        self._send_json({"error": "Export kind must be 'accounts' or 'users'"}, HTTPStatus.BAD_REQUEST)
                        return
                    csv_text = self.server.store.export_csv(host, kind)
                    if csv_text is None:
                        self._send_json({"error": "Host not found"}, HTTPStatus.NOT_FOUND)
                        return
                    filename = f"{host}_{kind}.csv"
                    self._send_bytes(
                        csv_text.encode("utf-8"),
                        "text/csv; charset=utf-8",
                        HTTPStatus.OK,
                        {
                            "Content-Disposition": f'attachment; filename="{filename}"',
                            "Cache-Control": "no-store",
                        },
                    )
                    return

            self._send_json({"error": "API endpoint not found"}, HTTPStatus.NOT_FOUND)
            return

        if path == "/" or path == "/index.html":
            self._send_file(STATIC_DIR / "index.html")
            return

        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            self._send_file(STATIC_DIR / rel)
            return

        self._send_file(STATIC_DIR / "index.html")

    # ------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        # Client bot ingest endpoint (supports TLS/mTLS + HMAC-SHA256 anti-replay + Bearer token)
        if path == "/api/report":
            raw_body = self._read_raw_body()
            if raw_body is None:
                return
            ok, reason = self.server.auth.verify_client_ingest(self.headers, raw_body)
            if not ok:
                self._send_json({"error": reason}, HTTPStatus.UNAUTHORIZED)
                return
            payload = self._parse_json_bytes(raw_body)
            if payload is None:
                return
            if not payload.get("host"):
                self._send_json({"error": "Missing required field: host"}, HTTPStatus.BAD_REQUEST)
                return
            res = self.server.store.save_report(payload)
            self.server.broker.publish(
                "report",
                {
                    "host": res["host"],
                    "id": res["id"],
                    "alerts_count": res["alerts_count"],
                    "received_at": res["received_at"],
                },
            )
            if res["new_alerts"]:
                dispatch_alerts_async(self.server.store.get_settings(), res["host"], res["new_alerts"])
            self._send_json(
                {
                    "ok": True,
                    "id": res["id"],
                    "host": res["host"],
                    "received_at": res["received_at"],
                    "auth_mode": reason,
                }
            )
            return

        if path == "/api/auth/login":
            payload = self._read_json_body()
            if payload is None:
                return
            if not self.server.auth.verify_admin_password(str(payload.get("password") or "")):
                self._send_json({"error": "Mật khẩu không đúng"}, HTTPStatus.UNAUTHORIZED)
                return
            tok = self.server.auth.create_session_token("admin")
            self._send_json(
                {"ok": True, "user": "admin"},
                HTTPStatus.OK,
                {"Set-Cookie": self.server.auth.build_set_cookie_header(tok, secure=self.server.config.tls_enabled)},
            )
            return

        if path == "/api/auth/logout":
            self._send_json(
                {"ok": True},
                HTTPStatus.OK,
                {"Set-Cookie": self.server.auth.build_clear_cookie_header(secure=self.server.config.tls_enabled)},
            )
            return

        if path == "/api/import":
            if not self._require_write_auth():
                return
            payload = self._read_json_body()
            if payload is None:
                return
            if not payload.get("host"):
                self._send_json({"error": "Invalid report JSON: missing 'host'"}, HTTPStatus.BAD_REQUEST)
                return
            res = self.server.store.save_report(payload)
            self.server.broker.publish(
                "report",
                {
                    "host": res["host"],
                    "id": res["id"],
                    "alerts_count": res["alerts_count"],
                    "received_at": res["received_at"],
                },
            )
            self._send_json({"ok": True, "id": res["id"], "host": res["host"], "received_at": res["received_at"]})
            return

        if path == "/api/alerts/ack":
            if self._require_ui_auth() is None:
                return
            payload = self._read_json_body()
            if payload is None:
                return
            host = str(payload.get("host") or "").strip()
            fp = str(payload.get("fingerprint") or "").strip()
            if not host or not fp:
                self._send_json({"error": "Missing host or fingerprint"}, HTTPStatus.BAD_REQUEST)
                return
            acked = bool(payload.get("acked", True))
            note = str(payload.get("note") or "")
            res = self.server.store.set_alert_ack(host, fp, acked=acked, note=note)
            self.server.broker.publish("alert_ack", res)
            self._send_json({"ok": True, **res})
            return

        if path == "/api/settings":
            if not self._require_write_auth():
                return
            payload = self._read_json_body()
            if payload is None:
                return
            updated = self.server.store.update_settings(payload)
            self._send_json({"ok": True, "settings": updated})
            return

        if path == "/api/settings/webhook-test":
            if not self._require_write_auth():
                return
            payload = self._read_json_body()
            if payload is None:
                return
            url = str(payload.get("webhook_url") or self.server.store.get_settings().get("webhook_url") or "").strip()
            ok, detail = send_webhook_sync(
                url,
                "test-host",
                [
                    {
                        "tag": "TEST",
                        "severity": "info",
                        "message": "Kiểm tra kết nối Webhook từ AccountHubIEC Monitor thành công!",
                    }
                ],
            )
            status = HTTPStatus.OK if ok else HTTPStatus.BAD_GATEWAY
            self._send_json({"ok": ok, "detail": detail}, status)
            return

        if path.startswith("/api/hosts/") and path.endswith("/note"):
            if self._require_ui_auth() is None:
                return
            parts = [unquote(p) for p in path[len("/api/hosts/") :].strip("/").split("/")]
            if len(parts) == 2 and parts[1] == "note":
                payload = self._read_json_body()
                if payload is None:
                    return
                ok = self.server.store.update_host_note(parts[0], str(payload.get("note") or ""))
                if not ok:
                    self._send_json({"error": "Host not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"ok": True, "host": parts[0]})
                return

        self._send_json({"error": "API endpoint not found"}, HTTPStatus.NOT_FOUND)

    # ------------------------------------------------------------ DELETE
    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/hosts/"):
            if not self._require_write_auth():
                return
            host = unquote(path[len("/api/hosts/") :].strip("/"))
            if not host or "/" in host:
                self._send_json({"error": "Invalid host"}, HTTPStatus.BAD_REQUEST)
                return
            deleted = self.server.store.delete_host(host)
            if not deleted:
                self._send_json({"error": "Host not found"}, HTTPStatus.NOT_FOUND)
                return
            self.server.broker.publish("host_deleted", {"host": host})
            self._send_json({"ok": True, "deleted": host})
            return

        self._send_json({"error": "Endpoint not found"}, HTTPStatus.NOT_FOUND)

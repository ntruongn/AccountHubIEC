#!/usr/bin/env python3
"""
server.py - Entrypoint cho AccountHubIEC Monitor Web Application.

Doc toan bo cau hinh (cong, TLS/mTLS, token HMAC, mat khau Web UI, SQLite)
tu file cau hinh (mac dinh: server_config.json) de tranh lo bi mat qua argv/ps aux.

Cach chay:
  python3 server.py
  python3 server.py --config /path/to/server_config.json
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from server_app.auth import AuthManager
from server_app.config import DEFAULT_SERVER_CONFIG, AppConfig
from server_app.http_server import MonitorWebServer
from server_app.sse import EventBroker
from server_app.store import MonitorStore, utc_now_iso


def import_report_file(store: MonitorStore, filepath: str) -> None:
    p = Path(filepath)
    if not p.is_file():
        print(f"[WARN] Khong tim thay file import: {filepath}", file=sys.stderr)
        return
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    res = store.save_report(data, received_at=data.get("timestamp") or utc_now_iso())
    print(f"[INFO] Da import bao cao tu {filepath} -> host={res['host']} (id={res['id']})")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="AccountHubIEC Monitor Web Application (config-file driven)")
    ap.add_argument(
        "-c",
        "--config",
        default=str(DEFAULT_SERVER_CONFIG),
        help=f"Duong dan file cau hinh JSON (mac dinh: {DEFAULT_SERVER_CONFIG.name})",
    )
    args = ap.parse_args(argv)

    try:
        cfg = AppConfig.from_file(args.config)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"[ERROR] {exc}")

    if cfg.tls_enabled_flag and (not cfg.tls_cert or not cfg.tls_key):
        sys.exit("[ERROR] tls.enabled=true nhung thieu tls.cert hoac tls.key trong config.")
    if cfg.tls_require_client_cert and not (cfg.tls_enabled and cfg.tls_ca_cert):
        sys.exit("[ERROR] tls.require_client_cert=true can bat tls.enabled va khai bao tls.ca_cert.")
    if cfg.require_hmac and not cfg.bot_token:
        sys.exit("[ERROR] security.require_hmac=true can khai bao security.token trong config.")

    store = MonitorStore(
        cfg.db_path,
        retain_days=cfg.retain_days,
        max_per_host=cfg.max_per_host,
        stale_seconds=cfg.stale_seconds,
    )
    for filepath in cfg.import_files:
        import_report_file(store, filepath)

    auth = AuthManager(
        cfg.bot_token,
        cfg.admin_password,
        cfg.session_secret,
        require_hmac=cfg.require_hmac,
        hmac_max_skew=cfg.hmac_max_skew,
    )
    broker = EventBroker()
    httpd = MonitorWebServer(cfg, store, auth, broker)

    def _handle_sigterm(_signum, _frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_sigterm)

    scheme = "https" if cfg.tls_enabled else "http"
    print("=" * 72)
    print(" AccountHubIEC Monitor Web Application")
    print(f" Config     : {cfg.config_path}")
    print(f" Web App    : {scheme}://localhost:{cfg.port}/")
    print(f" Ingest API : POST {scheme}://localhost:{cfg.port}/api/report")
    print(f" SSE Stream : GET  {scheme}://localhost:{cfg.port}/api/stream")
    print(f" SQLite DB  : {cfg.db_path}")
    print(
        f" Transport  : {'HTTPS (TLS 1.2+)' + (' + mTLS REQUIRED' if cfg.mtls_enabled else '') if cfg.tls_enabled else 'PLAIN HTTP (nen bat tls.enabled trong config)'}"
    )
    print(
        f" Bot Auth   : {'HMAC-SHA256 + Anti-Replay REQUIRED' if cfg.require_hmac else ('Bearer + HMAC supported' if auth.bot_auth_enabled else 'DISABLED (open)')}"
    )
    print(f" Web Login  : {'Password ENABLED' if auth.ui_auth_enabled else 'DISABLED (direct access)'}")
    print("=" * 72, flush=True)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Dang tat AccountHubIEC Monitor Server...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

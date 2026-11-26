"""CLI runner for AccountHubIEC multi-task monitoring client (config-file driven)."""
from __future__ import annotations

import json
import signal
import sys
import threading
from datetime import datetime

try:
    import psutil  # noqa: F401
except ImportError:
    sys.exit("Thieu psutil. Cai bang: pip install psutil")

from .config import load_config_from_cli
from .scheduler import MultiTaskCollector
from .sender import send_report


def main(argv: list[str] | None = None) -> None:
    try:
        cfg = load_config_from_cli(argv)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"[ERROR] {exc}")

    if bool(cfg.client_cert) ^ bool(cfg.client_key):
        sys.exit("[ERROR] Can khai bao ca tls.client_cert va tls.client_key trong config khi dung mTLS.")

    collector = MultiTaskCollector(cfg)
    stop_event = threading.Event()

    def _on_signal(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    collector.start()
    try:
        if cfg.once or not cfg.server:
            report = collector.collect_report(wait_disk_sec=cfg.disk_once_wait)
            text = json.dumps(report, indent=2, ensure_ascii=False)
            print(text)
            if cfg.output_file:
                with open(cfg.output_file, "w", encoding="utf-8") as f:
                    f.write(text)
            return

        if cfg.server.lower().startswith("http://"):
            print(
                "[WARN] Dang dung HTTP khong ma hoa. Nen chuyen server sang https:// va khai bao tls.ca_cert trong config.",
                file=sys.stderr,
                flush=True,
            )
        if cfg.insecure_tls:
            print(
                "[WARN] tls.insecure=true trong config: bo qua xac thuc chung chi TLS (khong nen dung trong production).",
                file=sys.stderr,
                flush=True,
            )

        auth_desc = "HMAC-SHA256" if (cfg.token and cfg.use_hmac) else ("Bearer" if cfg.token else "none")
        tls_desc = (
            "mTLS"
            if (cfg.client_cert and cfg.client_key)
            else ("HTTPS" if cfg.server.lower().startswith("https://") else "HTTP")
        )
        print(
            f"[{datetime.now():%H:%M:%S}] AccountHubIEC Client started | config={cfg.config_path} | "
            f"server={cfg.server} (transport={tls_desc}, auth={auth_desc}, fast={cfg.interval}s, disk_bg={cfg.disk_every}s)",
            flush=True,
        )

        while not stop_event.is_set():
            try:
                report = collector.collect_report(wait_disk_sec=0.0)
                status = send_report(
                    report,
                    cfg.server,
                    token=cfg.token,
                    timeout=cfg.http_timeout,
                    retries=cfg.http_retries,
                    use_hmac=cfg.use_hmac,
                    ca_cert=cfg.ca_cert,
                    client_cert=cfg.client_cert,
                    client_key=cfg.client_key,
                    insecure_tls=cfg.insecure_tls,
                )
                meta = report["collector_meta"]
                disk_st = meta["disk_scan"]
                print(
                    f"[{datetime.now():%H:%M:%S}] sent HTTP {status} | "
                    f"collect={meta['timings_ms']['total_ms']}ms | "
                    f"disk={disk_st['status']}({disk_st['scanned']}/{disk_st['total']}) | "
                    f"alerts={len(report['alerts'])}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[{datetime.now():%H:%M:%S}] error: {exc}", file=sys.stderr, flush=True)
            stop_event.wait(timeout=cfg.interval)
    finally:
        collector.stop()


if __name__ == "__main__":
    main()

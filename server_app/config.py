"""Configuration management for AccountHubIEC Monitor Web App (JSON config file driven)."""
from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_SERVER_CONFIG = BASE_DIR / "server_config.json"


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
class AppConfig:
    host: str = "0.0.0.0"
    port: int = 8443
    db_path: str = str(BASE_DIR / "monitor.db")
    bot_token: str = ""
    admin_password: str = ""
    session_secret: str = ""

    # Transport security (TLS / mTLS / HMAC anti-replay)
    tls_enabled_flag: bool = False
    tls_cert: str = ""
    tls_key: str = ""
    tls_ca_cert: str = ""
    tls_require_client_cert: bool = False
    require_hmac: bool = False
    hmac_max_skew: int = 300

    stale_seconds: int = 180
    retain_days: int = 14
    max_per_host: int = 10000
    max_body_bytes: int = 10 * 1024 * 1024
    import_files: list[str] = field(default_factory=list)
    config_path: str = ""

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_enabled_flag and self.tls_cert and self.tls_key)

    @property
    def mtls_enabled(self) -> bool:
        return bool(self.tls_enabled and self.tls_ca_cert and self.tls_require_client_cert)

    @classmethod
    def from_file(cls, config_path: str | Path | None = None) -> "AppConfig":
        cfg_file = Path(config_path) if config_path else DEFAULT_SERVER_CONFIG
        cfg_file = cfg_file.resolve()
        if not cfg_file.is_file():
            raise FileNotFoundError(
                f"Khong tim thay file cau hinh server: {cfg_file}\n"
                f"Hay tao tu mau: cp {BASE_DIR / 'server_config.example.json'} {cfg_file} && chmod 600 {cfg_file}"
            )

        with cfg_file.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"File cau hinh khong hop le (phai la JSON object): {cfg_file}")

        cfg_dir = cfg_file.parent
        tls = raw.get("tls") or {}
        sec = raw.get("security") or {}
        storage = raw.get("storage") or {}

        tls_cert = _resolve_path(cfg_dir, str(tls.get("cert") or ""))
        tls_key = _resolve_path(cfg_dir, str(tls.get("key") or ""))
        tls_ca = _resolve_path(cfg_dir, str(tls.get("ca_cert") or ""))
        tls_enabled_flag = bool(tls.get("enabled", bool(tls_cert and tls_key)))

        token = str(sec.get("token") or "")
        admin_pw = str(sec.get("admin_password") or "")
        session_secret = str(sec.get("session_secret") or os.environ.get("SESSION_SECRET") or "")
        if not session_secret:
            session_secret = os.urandom(32).hex()

        _warn_if_loose_permissions(cfg_file, has_secrets=bool(token or admin_pw))

        imports = [_resolve_path(cfg_dir, str(p)) for p in (raw.get("import_files") or []) if p]

        return cls(
            host=str(raw.get("host") or "0.0.0.0"),
            port=int(raw.get("port") or 8443),
            db_path=_resolve_path(cfg_dir, str(raw.get("db") or "monitor.db")),
            bot_token=token,
            admin_password=admin_pw,
            session_secret=session_secret,
            tls_enabled_flag=tls_enabled_flag,
            tls_cert=tls_cert,
            tls_key=tls_key,
            tls_ca_cert=tls_ca,
            tls_require_client_cert=bool(tls.get("require_client_cert", False)),
            require_hmac=bool(sec.get("require_hmac", False)),
            hmac_max_skew=max(30, int(sec.get("hmac_max_skew", 300))),
            stale_seconds=max(10, int(storage.get("stale_seconds", 180))),
            retain_days=max(1, int(storage.get("retain_days", 14))),
            max_per_host=max(100, int(storage.get("max_per_host", 10000))),
            import_files=imports,
            config_path=str(cfg_file),
        )

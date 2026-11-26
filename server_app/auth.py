"""Authentication & transport integrity helpers:
- Bearer token & HMAC-SHA256 request signing with timestamp + nonce anti-replay for client_bot
- Optional signed HttpOnly session cookie for Web UI
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from http.cookies import SimpleCookie


def compute_hmac_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    """Compute hex HMAC-SHA256 over `<timestamp>.<nonce>.<raw_body>`."""
    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(f"{timestamp}.{nonce}.".encode("utf-8"))
    mac.update(body)
    return mac.hexdigest()


class AuthManager:
    COOKIE_NAME = "ah_monitor_session"
    SESSION_TTL_SECONDS = 7 * 24 * 3600

    def __init__(
        self,
        bot_token: str,
        admin_password: str,
        session_secret: str,
        require_hmac: bool = False,
        hmac_max_skew: int = 300,
    ):
        self.bot_token = bot_token
        self.admin_password = admin_password
        self._secret = session_secret.encode("utf-8")
        self.require_hmac = bool(require_hmac)
        self.hmac_max_skew = max(30, int(hmac_max_skew))

        # Thread-safe nonce replay cache: {nonce: expires_at_unix}
        self._nonce_lock = threading.Lock()
        self._seen_nonces: dict[str, float] = {}

    @property
    def bot_auth_enabled(self) -> bool:
        return bool(self.bot_token)

    @property
    def ui_auth_enabled(self) -> bool:
        return bool(self.admin_password)

    def verify_bot_token(self, auth_header: str) -> bool:
        if not self.bot_token:
            return True
        header = (auth_header or "").strip()
        if not header.startswith("Bearer "):
            return False
        provided = header[7:].strip()
        return hmac.compare_digest(provided, self.bot_token)

    def verify_hmac_request(self, headers, body: bytes) -> tuple[bool, str]:
        """Verify HMAC-SHA256 signature, timestamp window, and nonce uniqueness."""
        if not self.bot_token:
            return False, "Server requires --token when HMAC verification is used"

        ts_str = (headers.get("X-Timestamp") or "").strip()
        nonce = (headers.get("X-Nonce") or "").strip()
        sig = (headers.get("X-Signature") or "").strip().lower()

        if not ts_str or not nonce or not sig:
            return False, "Missing X-Timestamp, X-Nonce, or X-Signature headers"
        if len(nonce) < 8 or len(nonce) > 128:
            return False, "Invalid X-Nonce length"
        try:
            ts_val = int(ts_str)
        except ValueError:
            return False, "Invalid X-Timestamp"

        now = time.time()
        if abs(now - ts_val) > self.hmac_max_skew:
            return False, f"Timestamp outside allowed skew ({self.hmac_max_skew}s)"

        expected = compute_hmac_signature(self.bot_token, ts_str, nonce, body)
        if not hmac.compare_digest(sig, expected):
            return False, "Invalid HMAC signature"

        # Check & record nonce to prevent replay within skew window
        with self._nonce_lock:
            expired = [k for k, exp in self._seen_nonces.items() if exp <= now]
            for k in expired:
                del self._seen_nonces[k]
            if nonce in self._seen_nonces:
                return False, "Replay detected (duplicate nonce)"
            self._seen_nonces[nonce] = now + self.hmac_max_skew + 30

        return True, "hmac-ok"

    def verify_client_ingest(self, headers, body: bytes) -> tuple[bool, str]:
        """
        Verify POST /api/report:
        - If X-Signature header is provided OR require_hmac=True -> enforce strict HMAC + anti-replay.
        - Otherwise fall back to Bearer token check (backward-compatible with legacy client_bot.py).
        """
        has_sig = bool(headers.get("X-Signature"))
        if self.require_hmac or has_sig:
            return self.verify_hmac_request(headers, body)
        if not self.verify_bot_token(headers.get("Authorization", "")):
            return False, "Unauthorized"
        return True, "bearer-ok"

    def verify_admin_password(self, password: str) -> bool:
        if not self.admin_password:
            return True
        return hmac.compare_digest(password or "", self.admin_password)

    def create_session_token(self, username: str = "admin") -> str:
        payload = {
            "u": username,
            "exp": int(time.time()) + self.SESSION_TTL_SECONDS,
        }
        raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
        sig = hmac.new(self._secret, raw.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{raw}.{sig}"

    def verify_session_cookie(self, cookie_header: str) -> dict | None:
        if not self.admin_password:
            return {"u": "admin", "auth": False}
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:  # noqa: BLE001
            return None
        morsel = cookie.get(self.COOKIE_NAME)
        if not morsel or "." not in morsel.value:
            return None
        raw, sig = morsel.value.rsplit(".", 1)
        expected = hmac.new(self._secret, raw.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data

    def build_set_cookie_header(self, token: str, secure: bool = False) -> str:
        sec = "; Secure" if secure else ""
        return (
            f"{self.COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax{sec}; "
            f"Max-Age={self.SESSION_TTL_SECONDS}"
        )

    def build_clear_cookie_header(self, secure: bool = False) -> str:
        sec = "; Secure" if secure else ""
        return f"{self.COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax{sec}; Max-Age=0"

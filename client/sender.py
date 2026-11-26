"""Secure HTTP/HTTPS JSON report sender with TLS 1.2+, optional mTLS, and HMAC-SHA256 anti-replay signing."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import ssl
import time
import urllib.error
import urllib.request


def build_client_ssl_context(
    ca_cert: str = "",
    client_cert: str = "",
    client_key: str = "",
    insecure: bool = False,
) -> ssl.SSLContext:
    if insecure:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif ca_cert:
        ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH, cafile=ca_cert)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    else:
        ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    if client_cert and client_key:
        ctx.load_cert_chain(certfile=client_cert, keyfile=client_key)
    return ctx


def sign_payload(secret: str, body: bytes) -> dict[str, str]:
    """Produce X-Timestamp, X-Nonce, X-Signature (HMAC-SHA256) without sending raw secret over the wire."""
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(f"{ts}.{nonce}.".encode("utf-8"))
    mac.update(body)
    return {
        "X-Timestamp": ts,
        "X-Nonce": nonce,
        "X-Signature": mac.hexdigest(),
    }


def send_report(
    report: dict,
    url: str,
    token: str = "",
    timeout: int = 15,
    retries: int = 3,
    use_hmac: bool = True,
    ca_cert: str = "",
    client_cert: str = "",
    client_key: str = "",
    insecure_tls: bool = False,
) -> int:
    data = json.dumps(report, ensure_ascii=False).encode("utf-8")
    ssl_ctx = (
        build_client_ssl_context(
            ca_cert=ca_cert,
            client_cert=client_cert,
            client_key=client_key,
            insecure=insecure_tls,
        )
        if url.lower().startswith("https://")
        else None
    )

    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AccountHubIEC-Client/2.1",
        }
        if token:
            if use_hmac:
                # Fresh timestamp & nonce on every attempt so retries are never flagged as replay
                headers.update(sign_payload(token, data))
            else:
                headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise
            last_err = exc
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        if attempt + 1 < retries:
            time.sleep(min(8, 2**attempt))

    assert last_err is not None
    raise last_err

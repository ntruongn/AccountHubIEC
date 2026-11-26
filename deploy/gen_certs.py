#!/usr/bin/env python3
"""
deploy/gen_certs.py - Sinh bo chung chi Private CA, Server TLS (co SAN IP/DNS) va Client mTLS.

Su dung OpenSSL de tao:
  - certs/ca.crt, certs/ca.key         (Private CA - copy ca.crt sang cac may client)
  - certs/server.crt, certs/server.key (Chung chi HTTPS cho server.py, co SAN IP/DNS)
  - certs/client.crt, certs/client.key (Chung chi mTLS tuy chon cho client/run.py)

Vi du:
  python3 deploy/gen_certs.py --ip 192.168.50.181 --ip 172.16.40.185 --dns monitor.local
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


def detect_local_ips() -> list[str]:
    ips: list[str] = ["127.0.0.1"]
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, timeout=5).strip()
        for tok in out.split():
            if "." in tok and tok not in ips:
                ips.append(tok)
    except Exception:  # noqa: BLE001
        pass
    return ips


def run_openssl(args: list[str]) -> None:
    res = subprocess.run(["openssl", *args], capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr + "\n")
        raise SystemExit(f"OpenSSL failed: openssl {' '.join(args)}")


def chmod_key(path: Path) -> None:
    os.chmod(path, 0o600)


def main() -> None:
    ap = argparse.ArgumentParser(description="Tao chung chi TLS/mTLS noi bo cho AccountHubIEC")
    ap.add_argument("--out-dir", default="certs", help="Thu muc luu chung chi (mac dinh: certs)")
    ap.add_argument("--ip", action="append", default=[], help="Them IP vao SAN (lap lai duoc)")
    ap.add_argument("--dns", action="append", default=[], help="Them ten mien vao SAN (lap lai duoc)")
    ap.add_argument("--client-name", default="accounthub-client", help="CN cho chung chi client mTLS")
    ap.add_argument("--days", type=int, default=825, help="Thoi han chung chi server/client (ngay)")
    ap.add_argument("--force", action="store_true", help="Ghi de neu thu muc certs da co file")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    ca_key = out_dir / "ca.key"
    ca_crt = out_dir / "ca.crt"
    srv_key = out_dir / "server.key"
    srv_crt = out_dir / "server.crt"
    cli_key = out_dir / "client.key"
    cli_crt = out_dir / "client.crt"

    if not args.force and (ca_crt.exists() or srv_crt.exists()):
        raise SystemExit(f"Chung chi da ton tai trong {out_dir}. Dung --force de tao lai.")

    ips = detect_local_ips()
    for ip in args.ip:
        if ip not in ips:
            ips.append(ip)

    dns_names = ["localhost", socket.gethostname()]
    for d in args.dns:
        if d not in dns_names:
            dns_names.append(d)

    san_entries = [f"DNS:{d}" for d in dns_names] + [f"IP:{ip}" for ip in ips]
    san_line = ",".join(san_entries)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)

        # 1. Private CA (ECDSA P-256)
        run_openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(ca_key)])
        chmod_key(ca_key)
        run_openssl(
            [
                "req", "-x509", "-new", "-sha256",
                "-key", str(ca_key),
                "-days", "3650",
                "-subj", "/CN=AccountHubIEC-Internal-CA/O=AccountHubIEC",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-out", str(ca_crt),
            ]
        )

        # 2. Server Certificate with SANs
        srv_csr = td_path / "server.csr"
        srv_ext = td_path / "server.ext"
        srv_ext.write_text(
            "\n".join(
                [
                    "basicConstraints=CA:FALSE",
                    "keyUsage=critical,digitalSignature,keyEncipherment",
                    "extendedKeyUsage=serverAuth",
                    f"subjectAltName={san_line}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        run_openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(srv_key)])
        chmod_key(srv_key)
        run_openssl(
            [
                "req", "-new", "-sha256",
                "-key", str(srv_key),
                "-subj", f"/CN={dns_names[0]}/O=AccountHubIEC",
                "-out", str(srv_csr),
            ]
        )
        run_openssl(
            [
                "x509", "-req", "-sha256",
                "-in", str(srv_csr),
                "-CA", str(ca_crt),
                "-CAkey", str(ca_key),
                "-CAcreateserial",
                "-CAserial", str(td_path / "ca.srl"),
                "-days", str(args.days),
                "-extfile", str(srv_ext),
                "-out", str(srv_crt),
            ]
        )

        # 3. Client Certificate for optional mTLS
        cli_csr = td_path / "client.csr"
        cli_ext = td_path / "client.ext"
        cli_ext.write_text(
            "\n".join(
                [
                    "basicConstraints=CA:FALSE",
                    "keyUsage=critical,digitalSignature",
                    "extendedKeyUsage=clientAuth",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        run_openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(cli_key)])
        chmod_key(cli_key)
        run_openssl(
            [
                "req", "-new", "-sha256",
                "-key", str(cli_key),
                "-subj", f"/CN={args.client_name}/O=AccountHubIEC",
                "-out", str(cli_csr),
            ]
        )
        run_openssl(
            [
                "x509", "-req", "-sha256",
                "-in", str(cli_csr),
                "-CA", str(ca_crt),
                "-CAkey", str(ca_key),
                "-CAserial", str(td_path / "ca.srl"),
                "-days", str(args.days),
                "-extfile", str(cli_ext),
                "-out", str(cli_crt),
            ]
        )

    print("=" * 68)
    print(f" Da tao bo chung chi TLS/mTLS tai: {out_dir}")
    print(f" SANs       : {san_line}")
    print(f" CA cert    : {ca_crt}  (copy file nay sang cac may client)")
    print(f" Server cert: {srv_crt} + {srv_key}")
    print(f" Client mTLS: {cli_crt} + {cli_key}  (tuy chon neu bat mTLS)")
    print("=" * 68)


if __name__ == "__main__":
    main()

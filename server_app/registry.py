"""Server-side Account & Research Group Registry (Key Members & Student Groups 1-3+ members).

Supports:
  - Importing legacy accounts from existing monitored hosts (Sync from `/home` telemetry)
  - Bulk CSV import & export (for migrating existing spreadsheets)
  - Direct lifecycle management (create, edit, assign members, renew/suspend/archive, audit log)
  - Real-time enrichment with live host telemetry (disk_mb, RAM, GPU VRAM, idle, days_inactive, alerts)
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

ACCOUNT_TYPES = ("key_member", "student_group", "admin", "other")
ACCOUNT_STATUSES = ("active", "unassigned", "suspended", "archived")
MEMBER_ROLES = ("leader", "member", "key_member")

SYSTEM_USERS = {
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news",
    "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats", "nobody",
    "systemd-network", "systemd-resolve", "systemd-timesync", "systemd-coredump",
    "messagebus", "syslog", "polkitd", "_apt", "uuidd", "tcpdump", "tss", "sshd",
    "avahi", "colord", "geoclue", "gdm", "rtkit", "ollama", "docker", "postgres",
    "mysql", "redis", "chrony", "ntpd", "postfix", "ntp", "whoopsie", "kernoops",
    "ubuntu", "lost+found",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date(val: str | None) -> date | None:
    s = (val or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def init_registry_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS managed_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host TEXT NOT NULL,
            username TEXT NOT NULL,
            account_type TEXT NOT NULL DEFAULT 'student_group',
            group_name TEXT NOT NULL DEFAULT '',
            advisor TEXT NOT NULL DEFAULT '',
            purpose TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            expires_at TEXT NOT NULL DEFAULT '',
            quota_disk_gb REAL,
            quota_gpu_note TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(host, username)
        );

        CREATE INDEX IF NOT EXISTS idx_managed_accounts_host
            ON managed_accounts(host);

        CREATE TABLE IF NOT EXISTS account_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            full_name TEXT NOT NULL,
            student_id TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'member',
            FOREIGN KEY (account_id) REFERENCES managed_accounts(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_account_members_acc
            ON account_members(account_id);

        CREATE TABLE IF NOT EXISTS account_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            command TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT 'admin',
            created_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES managed_accounts(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_account_actions_acc
            ON account_actions(account_id, id DESC);
        """
    )


class AccountRegistry:
    def __init__(self, store):
        self.store = store
        with self.store._lock, self._connect() as conn:
            init_registry_tables(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = self.store._connect()
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ------------------------------------------------------------ telemetry lookup
    def _build_telemetry_index(self, conn: sqlite3.Connection, host: str | None = None) -> dict[tuple[str, str], dict]:
        if host:
            rows = conn.execute("SELECT host, last_seen, latest_json FROM hosts WHERE host = ?", (host,)).fetchall()
        else:
            rows = conn.execute("SELECT host, last_seen, latest_json FROM hosts").fetchall()

        index: dict[tuple[str, str], dict] = {}
        for row in rows:
            h = row["host"]
            try:
                rep = json.loads(row["latest_json"])
            except json.JSONDecodeError:
                continue

            online_users = {o.get("user") for o in (rep.get("online_now") or [])}
            hub_by_sys = {
                x.get("system_user"): x
                for x in (rep.get("jupyterhub") or [])
                if isinstance(x, dict) and x.get("system_user")
            }

            for u in rep.get("users") or []:
                uname = u.get("username")
                if not uname:
                    continue
                index[(h, uname)] = {
                    "exists_on_host": True,
                    "home": u.get("home"),
                    "disk_mb": u.get("disk_mb"),
                    "days_inactive": u.get("days_inactive"),
                    "last_login": u.get("last_login"),
                    "online": uname in online_users,
                    "cpu_pct": 0.0,
                    "mem_mb": 0.0,
                    "gpu_mem_mb": 0,
                    "gpu_idle_mb": 0,
                    "procs": 0,
                    "heavy": False,
                    "hub_running": bool(hub_by_sys.get(uname, {}).get("server_running")),
                    "hub_idle_hours": hub_by_sys.get(uname, {}).get("idle_hours"),
                }

            for u in rep.get("usage_by_account") or []:
                uname = u.get("username")
                if not uname:
                    continue
                entry = index.setdefault(
                    (h, uname),
                    {
                        "exists_on_host": True,
                        "home": None,
                        "disk_mb": None,
                        "days_inactive": None,
                        "last_login": None,
                        "online": uname in online_users,
                        "hub_running": bool(hub_by_sys.get(uname, {}).get("server_running")),
                        "hub_idle_hours": hub_by_sys.get(uname, {}).get("idle_hours"),
                    },
                )
                entry.update(
                    {
                        "cpu_pct": float(u.get("cpu_pct") or 0.0),
                        "mem_mb": float(u.get("mem_mb") or 0.0),
                        "gpu_mem_mb": int(u.get("gpu_mem_mb") or 0),
                        "gpu_idle_mb": int(u.get("gpu_idle_mb") or 0),
                        "procs": int(u.get("procs") or 0),
                        "heavy": bool(u.get("heavy")),
                    }
                )
        return index

    def _enrich_expiry(self, acc: dict) -> None:
        exp_date = _parse_date(acc.get("expires_at"))
        if exp_date is None:
            acc["days_until_expiry"] = None
            acc["expiry_state"] = "none"
            return
        delta = (exp_date - date.today()).days
        acc["days_until_expiry"] = delta
        if delta < 0:
            acc["expiry_state"] = "expired"
        elif delta <= 14:
            acc["expiry_state"] = "expiring"
        else:
            acc["expiry_state"] = "valid"

    # ------------------------------------------------------------ queries
    def list_accounts(self, host: str | None = None) -> dict:
        with self._connect() as conn:
            if host:
                acc_rows = conn.execute(
                    "SELECT * FROM managed_accounts WHERE host = ? ORDER BY host ASC, username ASC",
                    (host,),
                ).fetchall()
            else:
                acc_rows = conn.execute(
                    "SELECT * FROM managed_accounts ORDER BY host ASC, username ASC"
                ).fetchall()

            mem_rows = conn.execute("SELECT * FROM account_members ORDER BY id ASC").fetchall()
            telem_index = self._build_telemetry_index(conn, host=host)

        members_by_acc: dict[int, list[dict]] = {}
        for m in mem_rows:
            members_by_acc.setdefault(m["account_id"], []).append(dict(m))

        accounts: list[dict] = []
        summary = {
            "total": 0,
            "key_members": 0,
            "student_groups": 0,
            "total_people": 0,
            "unassigned": 0,
            "expiring_or_expired": 0,
            "suspended": 0,
        }

        for r in acc_rows:
            acc = dict(r)
            members = members_by_acc.get(acc["id"], [])
            acc["members"] = members
            acc["telemetry"] = telem_index.get(
                (acc["host"], acc["username"]),
                {
                    "exists_on_host": False,
                    "disk_mb": None,
                    "days_inactive": None,
                    "last_login": None,
                    "online": False,
                    "cpu_pct": 0.0,
                    "mem_mb": 0.0,
                    "gpu_mem_mb": 0,
                    "gpu_idle_mb": 0,
                    "procs": 0,
                    "heavy": False,
                },
            )
            self._enrich_expiry(acc)
            accounts.append(acc)

            summary["total"] += 1
            summary["total_people"] += len(members)
            if acc["account_type"] == "key_member":
                summary["key_members"] += 1
            elif acc["account_type"] == "student_group":
                summary["student_groups"] += 1
            if acc["status"] == "unassigned":
                summary["unassigned"] += 1
            if acc["status"] == "suspended":
                summary["suspended"] += 1
            if acc["expiry_state"] in ("expiring", "expired") and acc["status"] not in ("archived", "suspended"):
                summary["expiring_or_expired"] += 1

        return {"accounts": accounts, "summary": summary}

    def get_account(self, account_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM managed_accounts WHERE id = ?", (account_id,)).fetchone()
            if not row:
                return None
            members = [
                dict(m)
                for m in conn.execute(
                    "SELECT * FROM account_members WHERE account_id = ? ORDER BY id ASC", (account_id,)
                ).fetchall()
            ]
            actions = [
                dict(a)
                for a in conn.execute(
                    "SELECT * FROM account_actions WHERE account_id = ? ORDER BY id DESC LIMIT 50",
                    (account_id,),
                ).fetchall()
            ]
            telem_index = self._build_telemetry_index(conn, host=row["host"])

        acc = dict(row)
        acc["members"] = members
        acc["actions"] = actions
        acc["telemetry"] = telem_index.get((acc["host"], acc["username"]), {"exists_on_host": False})
        self._enrich_expiry(acc)
        return acc

    def get_owner_map(self, host: str) -> dict[str, dict]:
        """Lightweight {username: owner_summary} for enriching Host / Accounts / Users telemetry views."""
        with self._connect() as conn:
            acc_rows = conn.execute(
                "SELECT id, username, account_type, group_name, advisor, status, expires_at "
                "FROM managed_accounts WHERE host = ?",
                (host,),
            ).fetchall()
            if not acc_rows:
                return {}
            ids = [r["id"] for r in acc_rows]
            placeholders = ",".join("?" for _ in ids)
            mem_rows = conn.execute(
                f"SELECT account_id, full_name, student_id, email, phone, role "
                f"FROM account_members WHERE account_id IN ({placeholders}) ORDER BY id ASC",
                ids,
            ).fetchall()

        mem_map: dict[int, list[dict]] = {}
        for m in mem_rows:
            mem_map.setdefault(m["account_id"], []).append(dict(m))

        out: dict[str, dict] = {}
        for r in acc_rows:
            d = dict(r)
            d["members"] = mem_map.get(r["id"], [])
            self._enrich_expiry(d)
            out[r["username"]] = d
        return out

    # ------------------------------------------------------------ mutations
    def _clean_members(self, members: list[dict] | None) -> list[dict]:
        cleaned: list[dict] = []
        for m in members or []:
            if not isinstance(m, dict):
                continue
            name = str(m.get("full_name") or "").strip()
            if not name:
                continue
            role = str(m.get("role") or "member").strip().lower()
            if role not in MEMBER_ROLES:
                role = "member"
            cleaned.append(
                {
                    "full_name": name,
                    "student_id": str(m.get("student_id") or "").strip(),
                    "email": str(m.get("email") or "").strip(),
                    "phone": str(m.get("phone") or "").strip(),
                    "role": role,
                }
            )
        return cleaned

    def upsert_account(self, payload: dict, actor: str = "admin") -> dict:
        host = str(payload.get("host") or "").strip()
        username = str(payload.get("username") or "").strip()
        if not host or not username:
            raise ValueError("Thiếu trường bắt buộc: host và username")

        acc_type = str(payload.get("account_type") or "student_group").strip()
        if acc_type not in ACCOUNT_TYPES:
            acc_type = "student_group"

        status = str(payload.get("status") or "active").strip()
        if status not in ACCOUNT_STATUSES:
            status = "active"

        group_name = str(payload.get("group_name") or "").strip()
        advisor = str(payload.get("advisor") or "").strip()
        purpose = str(payload.get("purpose") or "").strip()
        expires_at = str(payload.get("expires_at") or "").strip()[:10]
        if expires_at and _parse_date(expires_at) is None:
            raise ValueError("Định dạng ngày hết hạn phải là YYYY-MM-DD")

        quota_raw = payload.get("quota_disk_gb")
        quota_disk_gb = float(quota_raw) if quota_raw not in (None, "") else None
        quota_gpu_note = str(payload.get("quota_gpu_note") or "").strip()
        notes = str(payload.get("notes") or "").strip()
        source = str(payload.get("source") or "manual").strip()
        members = self._clean_members(payload.get("members"))

        # If admin assigned owner info to an unassigned account without changing status, mark it active
        if status == "unassigned" and (group_name or members or advisor):
            status = "active"

        now_iso = _utc_now_iso()
        acc_id = payload.get("id")

        with self.store._lock, self._connect() as conn:
            existing = None
            if acc_id is not None:
                existing = conn.execute("SELECT id FROM managed_accounts WHERE id = ?", (int(acc_id),)).fetchone()
            if existing is None:
                existing = conn.execute(
                    "SELECT id FROM managed_accounts WHERE host = ? AND username = ?",
                    (host, username),
                ).fetchone()

            if existing:
                target_id = existing["id"]
                conn.execute(
                    """
                    UPDATE managed_accounts
                    SET host = ?, username = ?, account_type = ?, group_name = ?,
                        advisor = ?, purpose = ?, status = ?, expires_at = ?,
                        quota_disk_gb = ?, quota_gpu_note = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        host, username, acc_type, group_name, advisor, purpose, status,
                        expires_at, quota_disk_gb, quota_gpu_note, notes, now_iso, target_id,
                    ),
                )
                action_type = "updated"
                summary = f"Cập nhật thông tin tài khoản ({acc_type}, {len(members)} thành viên, trạng thái: {status})"
            else:
                cur = conn.execute(
                    """
                    INSERT INTO managed_accounts (
                        host, username, account_type, group_name, advisor, purpose,
                        status, expires_at, quota_disk_gb, quota_gpu_note, notes,
                        source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        host, username, acc_type, group_name, advisor, purpose,
                        status, expires_at, quota_disk_gb, quota_gpu_note, notes,
                        source, now_iso, now_iso,
                    ),
                )
                target_id = cur.lastrowid
                action_type = "created"
                summary = f"Tạo mới bản ghi quản lý ({acc_type}, {len(members)} thành viên)"

            # Replace member list cleanly
            conn.execute("DELETE FROM account_members WHERE account_id = ?", (target_id,))
            for m in members:
                conn.execute(
                    """
                    INSERT INTO account_members (account_id, full_name, student_id, email, phone, role)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (target_id, m["full_name"], m["student_id"], m["email"], m["phone"], m["role"]),
                )

            conn.execute(
                """
                INSERT INTO account_actions (account_id, action_type, summary, command, actor, created_at)
                VALUES (?, ?, ?, '', ?, ?)
                """,
                (target_id, action_type, summary, actor, now_iso),
            )

        return self.get_account(target_id)

    def delete_account(self, account_id: int) -> bool:
        with self.store._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM managed_accounts WHERE id = ?", (account_id,))
            return cur.rowcount > 0

    def apply_lifecycle_action(self, account_id: int, payload: dict, actor: str = "admin") -> dict | None:
        """Apply direct lifecycle action (renew, suspend, activate, archive, note) and record audit trail."""
        action_type = str(payload.get("action_type") or "note").strip()
        summary = str(payload.get("summary") or "").strip()
        command = str(payload.get("command") or "").strip()
        renew_days = payload.get("renew_days")
        new_status = payload.get("status")
        now_iso = _utc_now_iso()

        with self.store._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM managed_accounts WHERE id = ?", (account_id,)).fetchone()
            if not row:
                return None

            updated_status = row["status"]
            updated_expires = row["expires_at"]

            if action_type == "renew" and renew_days:
                days = max(1, int(renew_days))
                base_dt = _parse_date(row["expires_at"])
                today = date.today()
                start = base_dt if (base_dt and base_dt > today) else today
                updated_expires = (start + timedelta(days=days)).isoformat()
                if updated_status in ("suspended", "archived"):
                    updated_status = "active"
                if not summary:
                    summary = f"Gia hạn thêm {days} ngày (hạn mới: {updated_expires})"
            elif action_type == "suspend":
                updated_status = "suspended"
                if not summary:
                    summary = "Tạm khóa tài khoản"
            elif action_type == "activate":
                updated_status = "active"
                if not summary:
                    summary = "Kích hoạt / mở khóa tài khoản"
            elif action_type == "archive":
                updated_status = "archived"
                if not summary:
                    summary = "Thu hồi & chuyển vào lưu trữ"
            elif new_status in ACCOUNT_STATUSES:
                updated_status = new_status

            if not summary:
                summary = f"Thực hiện thao tác: {action_type}"

            conn.execute(
                "UPDATE managed_accounts SET status = ?, expires_at = ?, updated_at = ? WHERE id = ?",
                (updated_status, updated_expires, now_iso, account_id),
            )
            conn.execute(
                """
                INSERT INTO account_actions (account_id, action_type, summary, command, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (account_id, action_type, summary, command, actor, now_iso),
            )

        return self.get_account(account_id)

    # ------------------------------------------------------------ legacy adoption: Sync & CSV
    def sync_from_hosts(self, host: str | None = None, actor: str = "admin") -> dict:
        """Import existing /home users from monitored hosts as 'unassigned' managed accounts."""
        now_iso = _utc_now_iso()
        added = 0
        skipped = 0
        synced_hosts: list[str] = []

        with self.store._lock, self._connect() as conn:
            if host:
                rows = conn.execute("SELECT host, latest_json FROM hosts WHERE host = ?", (host,)).fetchall()
            else:
                rows = conn.execute("SELECT host, latest_json FROM hosts").fetchall()

            for row in rows:
                h = row["host"]
                synced_hosts.append(h)
                try:
                    rep = json.loads(row["latest_json"])
                except json.JSONDecodeError:
                    continue

                candidates: set[str] = set()
                for u in rep.get("users") or []:
                    if u.get("username"):
                        candidates.add(str(u["username"]).strip())
                for u in rep.get("usage_by_account") or []:
                    uname = str(u.get("username") or "").strip()
                    if uname and uname not in SYSTEM_USERS:
                        candidates.add(uname)

                for uname in sorted(candidates):
                    if not uname or uname in SYSTEM_USERS:
                        continue
                    exists = conn.execute(
                        "SELECT id FROM managed_accounts WHERE host = ? AND username = ?",
                        (h, uname),
                    ).fetchone()
                    if exists:
                        skipped += 1
                        continue

                    # Heuristic default classification for legacy accounts
                    lower = uname.lower()
                    if "admin" in lower or "bot" in lower:
                        acc_type = "admin"
                    elif any(ch.isdigit() for ch in lower) and ("iec" in lower or "20" in lower):
                        acc_type = "student_group"
                    else:
                        acc_type = "key_member"

                    cur = conn.execute(
                        """
                        INSERT INTO managed_accounts (
                            host, username, account_type, group_name, advisor, purpose,
                            status, expires_at, quota_disk_gb, quota_gpu_note, notes,
                            source, created_at, updated_at
                        ) VALUES (?, ?, ?, '', '', '', 'unassigned', '', NULL, '', '', 'host_sync', ?, ?)
                        """,
                        (h, uname, acc_type, now_iso, now_iso),
                    )
                    conn.execute(
                        """
                        INSERT INTO account_actions (account_id, action_type, summary, command, actor, created_at)
                        VALUES (?, 'imported_from_host', ?, '', ?, ?)
                        """,
                        (cur.lastrowid, f"Đồng bộ tự động từ cụm máy {h} (chờ gán chủ sở hữu)", actor, now_iso),
                    )
                    added += 1

        return {"added": added, "skipped": skipped, "hosts": synced_hosts}

    def import_csv(self, csv_text: str, actor: str = "admin") -> dict:
        """
        Import legacy accounts + members from CSV.
        Supports:
          - Multiple rows with the same (host, username) -> automatically merges members!
          - Or a single row with semicolon-separated `members` column (`Nguyen Van A|2024001|a@uni.edu|0901; Tran Thi B|2024002`)
        """
        text = csv_text.lstrip("\ufeff").strip()
        if not text:
            raise ValueError("Nội dung CSV trống")

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV thiếu dòng tiêu đề (header)")

        grouped: dict[tuple[str, str], dict] = {}
        errors: list[str] = []

        for idx, raw_row in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items()}
            host = row.get("host") or ""
            username = row.get("username") or ""
            if not host or not username:
                errors.append(f"Dòng {idx}: thiếu host hoặc username")
                continue

            key = (host, username)
            if key not in grouped:
                acc_type = row.get("account_type") or "student_group"
                if acc_type in ("key", "keymember", "gv", "lecturer"):
                    acc_type = "key_member"
                elif acc_type in ("student", "group", "sv"):
                    acc_type = "student_group"
                grouped[key] = {
                    "host": host,
                    "username": username,
                    "account_type": acc_type if acc_type in ACCOUNT_TYPES else "student_group",
                    "group_name": row.get("group_name") or "",
                    "advisor": row.get("advisor") or "",
                    "purpose": row.get("purpose") or "",
                    "status": row.get("status") if row.get("status") in ACCOUNT_STATUSES else "active",
                    "expires_at": row.get("expires_at") or "",
                    "quota_disk_gb": row.get("quota_disk_gb") or "",
                    "quota_gpu_note": row.get("quota_gpu_note") or "",
                    "notes": row.get("notes") or "",
                    "source": "csv_import",
                    "members": [],
                }

            # Single member columns per row
            if row.get("member_name") or row.get("full_name"):
                grouped[key]["members"].append(
                    {
                        "full_name": row.get("member_name") or row.get("full_name"),
                        "student_id": row.get("student_id") or row.get("mssv") or "",
                        "email": row.get("email") or "",
                        "phone": row.get("phone") or "",
                        "role": row.get("role") or ("key_member" if grouped[key]["account_type"] == "key_member" else "member"),
                    }
                )

            # Compact semicolon-separated members column: "Name|MSSV|Email|Phone; Name2|MSSV2"
            if row.get("members"):
                for chunk in row["members"].split(";"):
                    parts = [p.strip() for p in chunk.split("|")]
                    if parts and parts[0]:
                        grouped[key]["members"].append(
                            {
                                "full_name": parts[0],
                                "student_id": parts[1] if len(parts) > 1 else "",
                                "email": parts[2] if len(parts) > 2 else "",
                                "phone": parts[3] if len(parts) > 3 else "",
                                "role": parts[4] if len(parts) > 4 and parts[4] in MEMBER_ROLES else "member",
                            }
                        )

        imported = 0
        for data in grouped.values():
            try:
                self.upsert_account(data, actor=actor)
                imported += 1
            except ValueError as exc:
                errors.append(f"{data['host']}/{data['username']}: {exc}")

        return {"imported": imported, "errors": errors}

    def export_csv(self, host: str | None = None) -> str:
        data = self.list_accounts(host=host)["accounts"]
        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow(
            [
                "host",
                "username",
                "account_type",
                "group_name",
                "advisor",
                "status",
                "expires_at",
                "quota_disk_gb",
                "purpose",
                "members",
                "disk_mb_live",
                "days_inactive_live",
                "mem_mb_live",
                "gpu_mem_mb_live",
                "notes",
            ]
        )
        for a in data:
            mem_str = "; ".join(
                "|".join([m["full_name"], m["student_id"], m["email"], m["phone"], m["role"]]).rstrip("|")
                for m in a["members"]
            )
            t = a.get("telemetry") or {}
            writer.writerow(
                [
                    a["host"],
                    a["username"],
                    a["account_type"],
                    a["group_name"],
                    a["advisor"],
                    a["status"],
                    a["expires_at"],
                    "" if a["quota_disk_gb"] is None else a["quota_disk_gb"],
                    a["purpose"],
                    mem_str,
                    "" if t.get("disk_mb") is None else t.get("disk_mb"),
                    "" if t.get("days_inactive") is None else t.get("days_inactive"),
                    t.get("mem_mb", 0),
                    t.get("gpu_mem_mb", 0),
                    a["notes"],
                ]
            )
        return buf.getvalue()

    @staticmethod
    def template_csv() -> str:
        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow(
            [
                "host",
                "username",
                "account_type",
                "group_name",
                "advisor",
                "status",
                "expires_at",
                "quota_disk_gb",
                "purpose",
                "members",
                "notes",
            ]
        )
        writer.writerow(
            [
                "bmtt",
                "jupyter-iec2024iot06",
                "student_group",
                "Nhóm IoT K2024 - Đề tài Smart Farm",
                "TS. Nguyễn Văn Hùng",
                "active",
                "2027-06-30",
                "100",
                "Nghiên cứu thị giác máy tính trên Edge AI",
                "Trần Minh Anh|20240101|anh.tm@uni.edu.vn|0901234567|leader; Lê Quốc Bảo|20240102|bao.lq@uni.edu.vn|0907654321|member",
                "Cấp 1 GPU RTX 4080 dùng chung",
            ]
        )
        writer.writerow(
            [
                "bmtt",
                "hunglk",
                "key_member",
                "Key Member - Lab IEC",
                "",
                "active",
                "",
                "300",
                "Giảng viên hướng dẫn chính",
                "Lê Khắc Hưng|GV012|hunglk@uni.edu.vn|0912345678|key_member",
                "Tài khoản dài hạn",
            ]
        )
        return buf.getvalue()

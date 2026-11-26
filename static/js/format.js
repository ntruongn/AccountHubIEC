/** Formatting helpers, HTML escaping, and system account classification. */

export const SYSTEM_ACCOUNTS = new Set([
  "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news",
  "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats", "nobody",
  "systemd-network", "systemd-resolve", "systemd-timesync", "systemd-coredump",
  "messagebus", "syslog", "polkitd", "_apt", "uuidd", "tcpdump", "tss", "sshd",
  "avahi", "colord", "geoclue", "gdm", "rtkit", "ollama", "docker", "postgres",
  "mysql", "redis", "chrony", "ntpd", "postfix", "ntp", "whoopsie", "kernoops",
]);

export function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function fmtMB(mb) {
  if (mb == null || Number.isNaN(Number(mb))) return "—";
  const v = Number(mb);
  if (v >= 1024 * 1024) return `${(v / 1024 / 1024).toFixed(2)} TB`;
  if (v >= 1024) return `${(v / 1024).toFixed(1)} GB`;
  return `${Math.round(v)} MB`;
}

export function fmtUptime(hours) {
  if (hours == null) return "—";
  const h = Number(hours);
  const days = Math.floor(h / 24);
  const rem = (h - days * 24).toFixed(1);
  return days > 0 ? `${days}d ${rem}h` : `${h.toFixed(1)}h`;
}

export function fmtAge(sec) {
  if (sec == null || sec < 0) return "—";
  if (sec < 60) return `${sec}s trước`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m trước`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}h trước`;
  return `${(sec / 86400).toFixed(1)}d trước`;
}

export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("vi-VN", {
    year: "2-digit", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

export function meterTone(pct, warn = 75, crit = 90) {
  const v = Number(pct || 0);
  if (v >= crit) return "crit";
  if (v >= warn) return "warn";
  return "ok";
}

export function severityBadgeClass(sev) {
  if (sev === "critical") return "badge-crit";
  if (sev === "warning") return "badge-warn";
  return "badge-info";
}

export function statusLabel(st) {
  if (st === "online") return "ONLINE";
  if (st === "stale") return "STALE";
  if (st === "offline") return "OFFLINE";
  return "UNKNOWN";
}

export async function copyText(text, toastFn, label = "Đã sao chép vào clipboard") {
  try {
    await navigator.clipboard.writeText(text);
    toastFn?.(label, "ok");
  } catch {
    toastFn?.("Trình duyệt chặn sao chép tự động", "err");
  }
}

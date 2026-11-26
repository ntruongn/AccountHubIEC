/** HTTP API client + SSE real-time stream connector. */

let bearerToken = sessionStorage.getItem("monitor_bot_token") || "";

export function setBearerToken(token) {
  bearerToken = (token || "").trim();
  if (bearerToken) sessionStorage.setItem("monitor_bot_token", bearerToken);
  else sessionStorage.removeItem("monitor_bot_token");
}

export function getBearerToken() {
  return bearerToken;
}

export async function api(url, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (bearerToken && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${bearerToken}`;
  }
  const res = await fetch(url, Object.assign({ credentials: "same-origin" }, opts, { headers }));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export async function apiPost(url, body) {
  return api(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function apiDelete(url) {
  return api(url, { method: "DELETE" });
}

export class LiveStream {
  constructor(onEvent, onStatus) {
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.es = null;
    this.closed = false;
  }

  connect() {
    this.disconnect();
    this.closed = false;
    try {
      const es = new EventSource("/api/stream");
      this.es = es;
      es.addEventListener("connected", () => this.onStatus("online", "SSE LIVE"));
      es.addEventListener("report", (ev) => this.onEvent("report", JSON.parse(ev.data)));
      es.addEventListener("alert_ack", (ev) => this.onEvent("alert_ack", JSON.parse(ev.data)));
      es.addEventListener("host_deleted", (ev) => this.onEvent("host_deleted", JSON.parse(ev.data)));
      es.onerror = () => {
        if (!this.closed) this.onStatus("stale", "SSE đang kết nối lại...");
      };
    } catch {
      this.onStatus("offline", "SSE không khả dụng");
    }
  }

  disconnect() {
    this.closed = true;
    if (this.es) {
      this.es.close();
      this.es = null;
    }
  }
}

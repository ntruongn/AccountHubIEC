/** Main SPA Controller: Hash Router, SSE Live Push, Theme Manager, Auth Session, and View Orchestration. */
import { api, apiPost, LiveStream, setBearerToken } from "./api.js";
import { esc } from "./format.js";
import { renderAccountsView } from "./views/accounts.js";
import { renderAlertsView } from "./views/alerts.js";
import { renderFleetView } from "./views/fleet.js";
import { renderHostView } from "./views/host.js";
import { renderRegistryView } from "./views/registry.js";
import { renderSettingsView } from "./views/settings.js";
import { renderUsersView } from "./views/users.js";

const $ = (id) => document.getElementById(id);

const ROUTE_META = {
  fleet: ["Toàn hệ thống (Fleet)", "Tổng quan cụm máy chủ & GPU"],
  host: ["Chi tiết Máy chủ", "CPU, RAM, GPU, Ổ đĩa & Time-Travel Snapshot"],
  accounts: ["Tài nguyên & Tiến trình", "Phân tích sử dụng tài nguyên theo tài khoản"],
  users: ["Tài khoản /home & Hub", "Kiểm toán user không hoạt động, disk và JupyterHub"],
  registry: ["Quản lý Tài khoản & Nhóm", "Key Members, Nhóm sinh viên (1–3+ SV), đồng bộ cũ & vòng đời"],
  alerts: ["Trung tâm Cảnh báo", "Cảnh báo đang hoạt động & nhật ký sự cố"],
  settings: ["Cài đặt & Triển khai", "Kết nối client/config.json, Webhook và quản lý dữ liệu"],
};

const app = {
  route: "fleet",
  selectedHost: null,
  snapshotId: null,
  historyHours: 24,
  fleet: null,
  latest: null,
  history: [],
  snapshots: [],
  alerts: [],
  authMeta: { ui_auth_enabled: false, bot_auth_enabled: false, authenticated: true },
  stream: null,
  refreshDebounce: null,
  pendingRender: false,
};

function hasOpenModal() {
  const portal = $("modal-portal");
  return Boolean(portal && portal.querySelector(".modal-backdrop")) ||
    Boolean(document.querySelector("#view-root .modal-backdrop"));
}

function isUserTypingInView() {
  const el = document.activeElement;
  const root = $("view-root");
  if (!el || !root || !root.contains(el)) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

function isUserBusy() {
  return hasOpenModal() || isUserTypingInView();
}

function toast(msg, kind = "ok") {
  const box = $("toast-region");
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 3600);
}

// --- Theme Management ---
function applyTheme(theme) {
  const mode = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", mode);
  localStorage.setItem("ah_theme", mode);
  $("icon-theme-sun").classList.toggle("hidden", mode !== "dark");
  $("icon-theme-moon").classList.toggle("hidden", mode === "dark");
}

// --- Routing ---
function parseHash() {
  const raw = (window.location.hash || "#/fleet").replace(/^#\/?/, "");
  const parts = raw.split("/").filter(Boolean);
  const route = ROUTE_META[parts[0]] ? parts[0] : "fleet";
  const hostParam = route === "host" && parts[1] ? decodeURIComponent(parts[1]) : null;
  return { route, hostParam };
}

function navigate(hash) {
  if (window.location.hash === hash) onRouteChange();
  else window.location.hash = hash;
}

// --- Sidebar & Topbar Sync ---
function syncChrome() {
  const [title, sub] = ROUTE_META[app.route] || ROUTE_META.fleet;
  $("page-title").textContent = app.route === "host" && app.selectedHost ? `Máy chủ: ${app.selectedHost}` : title;
  $("page-subtitle").textContent = sub;

  document.querySelectorAll("#sidebar-nav .nav-item").forEach((a) => {
    a.classList.toggle("active", a.dataset.route === app.route);
  });

  const hosts = app.fleet?.hosts || [];
  $("nav-count-hosts").textContent = hosts.length;
  $("nav-count-accounts").textContent = app.latest?.report?.usage_by_account?.length || 0;
  $("nav-count-users").textContent = app.latest?.report?.users?.length || 0;

  const activeAlerts = (app.alerts || []).filter((a) => !a.acked).length;
  const alBadge = $("nav-count-alerts");
  alBadge.textContent = activeAlerts;
  alBadge.classList.toggle("has-items", activeAlerts > 0);
  if ($("nav-count-registry")) {
    $("nav-count-registry").textContent = app.registryCount ?? 0;
  }

  // Sidebar Host List
  const sideList = $("sidebar-host-list");
  sideList.innerHTML = hosts.length === 0
    ? `<div class="muted-sm" style="padding:6px 10px">Chưa có host</div>`
    : hosts.map((h) => `
      <button type="button" class="side-host-btn ${h.host === app.selectedHost ? "active" : ""}" data-pick-host="${esc(h.host)}">
        <span class="row-wrap" style="gap:7px;min-width:0">
          <span class="status-dot status-${esc(h.status)}" aria-hidden="true"></span>
          <span style="overflow:hidden;text-overflow:ellipsis">${esc(h.host)}</span>
        </span>
        ${h.alerts_count > 0 ? `<span class="badge badge-crit" style="padding:0 5px">${h.alerts_count}</span>` : ""}
      </button>`).join("");

  sideList.querySelectorAll("[data-pick-host]").forEach((btn) => {
    btn.addEventListener("click", () => {
      app.selectedHost = btn.dataset.pickHost;
      app.snapshotId = null;
      closeMobileSidebar();
      if (app.route === "fleet") navigate(`#/host/${encodeURIComponent(app.selectedHost)}`);
      else refreshData(false);
    });
  });

  // Topbar Host Dropdown
  const sel = $("topbar-host-select");
  const wrap = $("topbar-host-wrap");
  wrap.classList.toggle("hidden", hosts.length === 0);
  sel.innerHTML = hosts.map((h) => `<option value="${esc(h.host)}" ${h.host === app.selectedHost ? "selected" : ""}>${esc(h.host)} (${h.status})</option>`).join("");
}

// --- Data Fetching ---
async function refreshData(silent = false, forceRender = false) {
  try {
    const [fleetRes, alertsRes, regRes] = await Promise.all([
      api("/api/fleet"),
      api("/api/alerts"),
      api("/api/registry/accounts").catch(() => ({ summary: { total: 0 } })),
    ]);
    app.fleet = fleetRes;
    app.alerts = alertsRes.alerts || [];
    app.registryCount = regRes?.summary?.total ?? 0;

    const hosts = fleetRes.hosts || [];
    if (hosts.length === 0) {
      app.selectedHost = null;
      app.latest = null;
      app.history = [];
      app.snapshots = [];
    } else if (!app.selectedHost || !hosts.some((h) => h.host === app.selectedHost)) {
      app.selectedHost = hosts[0].host;
      app.snapshotId = null;
    }

    if (app.selectedHost) {
      const enc = encodeURIComponent(app.selectedHost);
      const snapQs = app.snapshotId ? `?snapshot_id=${app.snapshotId}` : "";
      const [latest, histRes, snapRes] = await Promise.all([
        api(`/api/hosts/${enc}/latest${snapQs}`),
        api(`/api/hosts/${enc}/history?hours=${app.historyHours}&limit=360`),
        api(`/api/hosts/${enc}/snapshots?limit=50`),
      ]);
      app.latest = latest;
      app.history = histRes.points || [];
      app.snapshots = snapRes.snapshots || [];
    }

    syncChrome();

    // Never wipe out an open popup/modal or active form input during background SSE/timer updates
    if (silent && !forceRender && isUserBusy()) {
      app.pendingRender = true;
      return;
    }
    app.pendingRender = false;
    renderActiveView();
  } catch (err) {
    if (err.status === 401) {
      showAuthModal();
      return;
    }
    if (!silent) toast(`Lỗi tải dữ liệu: ${err.message}`, "err");
  }
}

function renderActiveView() {
  const root = $("view-root");
  const ctx = {
    fleet: app.fleet,
    latest: app.latest,
    history: app.history,
    snapshots: app.snapshots,
    alerts: app.alerts,
    selectedHost: app.selectedHost,
    snapshotId: app.snapshotId,
    historyHours: app.historyHours,
    modalRoot: $("modal-portal"),
    toast,
    navigate,
    refresh: () => refreshData(false, true),
    refreshRegistryBadge: () => api("/api/registry/accounts").then((r) => {
      app.registryCount = r?.summary?.total ?? 0;
      if ($("nav-count-registry")) $("nav-count-registry").textContent = app.registryCount;
    }).catch(() => {}),
    triggerImport: () => $("global-file-import").click(),
    importFile,
    setSnapshot: (id) => { app.snapshotId = id; refreshData(false, true); },
    setHistoryHours: (hours) => { app.historyHours = hours; refreshData(false, true); },
  };

  switch (app.route) {
    case "fleet": renderFleetView(root, ctx); break;
    case "host": renderHostView(root, ctx); break;
    case "accounts": renderAccountsView(root, ctx); break;
    case "users": renderUsersView(root, ctx); break;
    case "registry": renderRegistryView(root, ctx); break;
    case "alerts": renderAlertsView(root, ctx); break;
    case "settings": renderSettingsView(root, ctx); break;
    default: renderFleetView(root, ctx);
  }
}

function onRouteChange() {
  const { route, hostParam } = parseHash();
  app.route = route;
  if (hostParam && hostParam !== app.selectedHost) {
    app.selectedHost = hostParam;
    app.snapshotId = null;
  }
  const portal = $("modal-portal");
  if (portal) portal.innerHTML = "";
  closeMobileSidebar();
  refreshData(true, true);
}

// --- JSON Import ---
async function importFile(file) {
  if (!file) return;
  try {
    const text = await file.text();
    const payload = JSON.parse(text);
    if (!payload.host) throw new Error("File JSON thiếu trường 'host'");

    let res;
    try {
      res = await apiPost("/api/import", payload);
    } catch (err) {
      if (err.status === 401 && app.authMeta.bot_auth_enabled && !app.authMeta.ui_auth_enabled) {
        const tok = window.prompt("Server đang bật --token. Nhập Bearer token để import:");
        if (!tok) return;
        setBearerToken(tok);
        res = await apiPost("/api/import", payload);
      } else {
        throw err;
      }
    }
    app.selectedHost = res.host;
    app.snapshotId = null;
    toast(`Đã import báo cáo cho host "${res.host}"`, "ok");
    navigate(`#/host/${encodeURIComponent(res.host)}`);
  } catch (err) {
    toast(`Import thất bại: ${err.message}`, "err");
  }
}

// --- Auth Overlay ---
function showAuthModal() {
  $("auth-overlay").classList.remove("hidden");
  $("auth-password").focus();
}

function hideAuthModal() {
  $("auth-overlay").classList.add("hidden");
  $("auth-error").classList.add("hidden");
  $("auth-password").value = "";
}

// --- Mobile Sidebar ---
function openMobileSidebar() {
  $("sidebar").classList.add("open");
  $("sidebar-backdrop").classList.remove("hidden");
}
function closeMobileSidebar() {
  $("sidebar").classList.remove("open");
  $("sidebar-backdrop").classList.add("hidden");
}

// --- SSE Live Connection ---
function startLiveStream() {
  if (app.stream) app.stream.disconnect();
  app.stream = new LiveStream(
    (eventName, data) => {
      if (eventName === "report") {
        toast(`Nhận báo cáo mới từ ${data.host}`, "ok");
      }
      clearTimeout(app.refreshDebounce);
      app.refreshDebounce = setTimeout(() => refreshData(true), 250);
    },
    (status, text) => {
      $("sse-dot").className = `status-dot status-${status}`;
      $("sse-text").textContent = text;
    }
  );
  app.stream.connect();
}

// --- Bootstrap ---
async function boot() {
  applyTheme(localStorage.getItem("ah_theme") || "dark");

  // Bind UI controls
  $("btn-theme").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    applyTheme(cur === "dark" ? "light" : "dark");
    renderActiveView();
  });

  $("btn-refresh").addEventListener("click", () => refreshData(false));
  $("btn-quick-import").addEventListener("click", () => $("global-file-import").click());
  $("global-file-import").addEventListener("change", (e) => {
    importFile(e.target.files?.[0]);
    e.target.value = "";
  });

  $("topbar-host-select").addEventListener("change", (e) => {
    app.selectedHost = e.target.value;
    app.snapshotId = null;
    if (app.route === "host") navigate(`#/host/${encodeURIComponent(app.selectedHost)}`);
    else refreshData(false);
  });

  $("btn-open-sidebar").addEventListener("click", openMobileSidebar);
  $("btn-close-sidebar").addEventListener("click", closeMobileSidebar);
  $("sidebar-backdrop").addEventListener("click", closeMobileSidebar);

  $("auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await apiPost("/api/auth/login", { password: $("auth-password").value });
      hideAuthModal();
      $("btn-logout").classList.remove("hidden");
      startLiveStream();
      onRouteChange();
    } catch (err) {
      const errEl = $("auth-error");
      errEl.textContent = err.message || "Đăng nhập thất bại";
      errEl.classList.remove("hidden");
    }
  });

  $("btn-logout").addEventListener("click", async () => {
    await apiPost("/api/auth/logout", {}).catch(() => {});
    app.stream?.disconnect();
    showAuthModal();
  });

  window.addEventListener("hashchange", onRouteChange);
  window.addEventListener("resize", () => {
    if (!isUserBusy() && (app.route === "fleet" || app.route === "host" || app.route === "accounts")) {
      renderActiveView();
    }
  });

  // Flush any deferred background refresh right after a modal closes
  const modalPortal = $("modal-portal");
  if (modalPortal) {
    new MutationObserver(() => {
      if (app.pendingRender && !isUserBusy()) {
        app.pendingRender = false;
        renderActiveView();
      }
    }).observe(modalPortal, { childList: true });
  }

  // Periodic safety refresh (updates relative timestamps & stale detection)
  setInterval(() => {
    if (document.visibilityState === "visible" && $("auth-overlay").classList.contains("hidden")) {
      refreshData(true);
    }
  }, 30000);

  // Check Auth & Launch
  try {
    app.authMeta = await api("/api/auth/me");
    if (app.authMeta.ui_auth_enabled) {
      $("btn-logout").classList.toggle("hidden", !app.authMeta.authenticated);
      if (!app.authMeta.authenticated) {
        showAuthModal();
        return;
      }
    }
  } catch {
    // Continue and let refreshData show error if offline
  }

  startLiveStream();
  onRouteChange();
}

boot();

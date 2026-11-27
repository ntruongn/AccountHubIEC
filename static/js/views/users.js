/** View: Users & Storage Audit — Activity Donut chart, /home users table with CSV export, Online who, JupyterHub. */
import { renderDonutSVG } from "../charts.js";
import { esc, fmtDateTime, fmtMB } from "../format.js";

const localState = {
  search: "",
  bucket: "all",
  sort: { key: "days_inactive", dir: "desc" },
};

function classifyBucket(u) {
  if (u.days_inactive == null) return "never";
  const d = Number(u.days_inactive);
  if (d < 7) return "active";
  if (d < 30) return "recent";
  if (d < 90) return "idle";
  return "stale";
}

export function renderUsersView(root, ctx) {
  const latest = ctx.latest;
  if (!latest) {
    root.innerHTML = `<div class="card"><p class="muted">Chưa có dữ liệu máy chủ.</p></div>`;
    return;
  }

  const rep = latest.report;
  const ownerMap = latest.owner_map || {};
  const users = rep.users || [];
  const online = rep.online_now || [];
  const hubs = rep.jupyterhub || [];

  const counts = { active: 0, recent: 0, idle: 0, stale: 0, never: 0 };
  users.forEach((u) => { counts[classifyBucket(u)] += 1; });

  const segments = [
    { id: "active", label: "Hoạt động (< 7 ngày)", value: counts.active, color: "#22c55e" },
    { id: "recent", label: "Gần đây (7–29 ngày)", value: counts.recent, color: "#38bdf8" },
    { id: "idle", label: "Ít dùng (30–89 ngày)", value: counts.idle, color: "#f59e0b" },
    { id: "stale", label: "Lâu không dùng (≥ 90d)", value: counts.stale, color: "#ef4444" },
    { id: "never", label: "Chưa từng đăng nhập", value: counts.never, color: "#64748b" },
  ];

  const q = localState.search.trim().toLowerCase();
  let filtered = users.slice();
  if (localState.bucket !== "all") filtered = filtered.filter((u) => classifyBucket(u) === localState.bucket);
  if (q) filtered = filtered.filter((u) => (u.username || "").toLowerCase().includes(q));

  const { key, dir } = localState.sort;
  filtered.sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "string") return dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    return dir === "asc" ? Number(av) - Number(bv) : Number(bv) - Number(av);
  });

  root.innerHTML = `
    <div class="grid-asymmetric">
      <!-- /home Users Audit Table -->
      <section class="card">
        <div class="card-head">
          <div>
            <h2 class="card-title">Kiểm toán tài khoản /home (${users.length})</h2>
            <p class="card-sub">Theo dõi tài khoản không hoạt động lâu ngày và dung lượng thư mục cá nhân</p>
          </div>
          <div class="row-wrap">
            <input type="search" id="usr-search" class="input" style="min-width:170px" value="${esc(localState.search)}" placeholder="Lọc username...">
            <a href="/api/hosts/${encodeURIComponent(latest.host)}/export/users" class="btn btn-secondary btn-sm">Xuất CSV</a>
          </div>
        </div>

        <div class="row-wrap" style="margin-bottom:12px" id="usr-bucket-chips">
          <button type="button" class="btn btn-sm ${localState.bucket === "all" ? "btn-primary" : "btn-secondary"}" data-bucket="all">Tất cả (${users.length})</button>
          ${segments.map((s) => `
            <button type="button" class="btn btn-sm ${localState.bucket === s.id ? "btn-primary" : "btn-secondary"}" data-bucket="${s.id}">
              ${esc(s.label.split("(")[0].trim())} (${s.value})
            </button>`).join("")}
        </div>

        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th class="sortable ${key === "username" ? `sort-${dir}` : ""}" data-sort="username">Username</th>
                <th class="sortable num ${key === "days_inactive" ? `sort-${dir}` : ""}" data-sort="days_inactive">Không hoạt động</th>
                <th class="sortable num ${key === "disk_mb" ? `sort-${dir}` : ""}" data-sort="disk_mb">Dung lượng /home</th>
                <th>Đăng nhập gần nhất</th>
              </tr>
            </thead>
            <tbody>
              ${filtered.length === 0
                ? `<tr><td colspan="4" class="muted-sm" style="text-align:center;padding:18px">Không có user nào khớp bộ lọc.</td></tr>`
                : filtered.map((u) => {
                    let badge = `<span class="badge badge-muted">Chưa đăng nhập</span>`;
                    if (u.days_inactive != null) {
                      const d = Number(u.days_inactive);
                      const cls = d >= 90 ? "badge-crit" : d >= 30 ? "badge-warn" : "badge-ok";
                      badge = `<span class="badge ${cls}">${d} ngày</span>`;
                    }
                    const diskHtml = u.disk_mb == null
                      ? `<span class="mono-muted" title="Chạy client_bot.py bằng sudo để đo du -sm đầy đủ">N/A (cần sudo)</span>`
                      : `<strong>${fmtMB(u.disk_mb)}</strong>`;
                    const owner = ownerMap[u.username];
                    const ownerHtml = owner
                      ? `<div class="muted-sm" style="margin-top:2px;color:var(--info-text)">${esc(owner.group_name || owner.account_type)}${owner.members?.length ? ` · ${esc(owner.members.map((m) => `${m.full_name}${m.student_id ? ` (${m.student_id})` : ""}`).join(", "))}` : ""}</div>`
                      : `<div class="muted-sm"><a href="#/registry" style="color:var(--fg-subtle);text-decoration:none">Chưa gán chủ →</a></div>`;
                    return `
                      <tr>
                        <td class="mono"><strong>${esc(u.username)}</strong><div class="muted-sm">${esc(u.home)}</div>${ownerHtml}</td>
                        <td class="num">${badge}</td>
                        <td class="num">${diskHtml}</td>
                        <td class="mono-muted" style="font-size:11.5px">${esc(u.last_login || "Never logged in")}</td>
                      </tr>`;
                  }).join("")}
            </tbody>
          </table>
        </div>
      </section>

      <!-- Right Column: Donut + Online + JupyterHub -->
      <div class="stack">
        <article class="card">
          <div class="card-head">
            <h3 class="card-title">Phân bố hoạt động tài khoản</h3>
            <span class="badge ${counts.stale > 0 ? "badge-warn" : "badge-ok"}">${counts.stale} ≥ 90 ngày</span>
          </div>
          <div class="donut-layout">
            ${renderDonutSVG(segments, "Users")}
            <div class="donut-legend">
              ${segments.map((s) => `
                <div class="donut-row">
                  <span><span class="swatch" style="background:${s.color}"></span>${esc(s.label)}</span>
                  <strong class="mono">${s.value}</strong>
                </div>`).join("")}
            </div>
          </div>
        </article>

        <article class="card">
          <div class="card-head">
            <h3 class="card-title">Phiên đang đăng nhập (who)</h3>
            <span class="mono-muted">${online.length} phiên</span>
          </div>
          <div class="stack">
            ${online.length === 0 ? `<p class="muted-sm" style="margin:0">Không có phiên nào đang mở.</p>` : online.map((s) => `
              <div class="item-box row-between">
                <div>
                  <strong class="mono">${esc(s.user)}</strong>
                  <div class="mono-muted">${esc(s.terminal || "tty")} ${s.host ? `· ${esc(s.host)}` : ""}</div>
                </div>
                <span class="badge badge-ok">${fmtDateTime(s.since)}</span>
              </div>`).join("")}
          </div>
        </article>

        <article class="card">
          <div class="card-head">
            <h3 class="card-title">JupyterHub Servers</h3>
            <span class="badge ${rep.system?.jupyterhub_service === "active" ? "badge-ok" : "badge-muted"}">${esc(rep.system?.jupyterhub_service || "unknown")}</span>
          </div>
          ${hubs.length === 0
            ? `<p class="muted-sm" style="margin:0">Chưa cấu hình <code>--hub-url</code> / <code>--hub-token</code> trên client_bot.py (hoặc không có notebook server nào).</p>`
            : hubs[0]?.error
              ? `<div class="alert-row sev-warning"><span class="badge badge-warn">HUB API</span><span>${esc(hubs[0].error)}</span></div>`
              : `<div class="stack">${hubs.map((h) => `
                  <div class="item-box row-between">
                    <div><strong class="mono">${esc(h.name)}</strong><div class="mono-muted">Idle: ${h.idle_hours != null ? `${h.idle_hours}h` : "—"}</div></div>
                    <span class="badge ${h.server_running ? "badge-ok" : "badge-muted"}">${h.server_running ? "Running" : "Stopped"}</span>
                  </div>`).join("")}</div>`}
        </article>
      </div>
    </div>
  `;

  root.querySelector("#usr-search")?.addEventListener("input", (e) => {
    localState.search = e.target.value;
    renderUsersView(root, ctx);
    const input = root.querySelector("#usr-search");
    input?.focus();
    input?.setSelectionRange(input.value.length, input.value.length);
  });

  root.querySelectorAll("#usr-bucket-chips [data-bucket]").forEach((btn) => {
    btn.addEventListener("click", () => {
      localState.bucket = btn.dataset.bucket;
      renderUsersView(root, ctx);
    });
  });

  root.querySelectorAll("th.sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      localState.sort = {
        key: k,
        dir: localState.sort.key === k && localState.sort.dir === "desc" ? "asc" : "desc",
      };
      renderUsersView(root, ctx);
    });
  });
}

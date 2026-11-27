/** View: Accounts & Processes — Top Consumers chart, sortable table, expandable top_procs & per-account trend chart. */
import { api } from "../api.js";
import { drawLineChart } from "../charts.js";
import { copyText, esc, fmtMB, fmtUptime, SYSTEM_ACCOUNTS } from "../format.js";

const localState = {
  topMetric: "mem_mb",
  search: "",
  hideSystem: true,
  onlyFlagged: false,
  sort: { key: "mem_mb", dir: "desc" },
  expanded: new Set(),
};

export function renderAccountsView(root, ctx) {
  const latest = ctx.latest;
  if (!latest) {
    root.innerHTML = `<div class="card"><p class="muted">Chưa có dữ liệu máy chủ.</p></div>`;
    return;
  }

  const rep = latest.report;
  const ownerMap = latest.owner_map || {};
  const allAccounts = rep.usage_by_account || [];
  const onlineUsers = new Set((rep.online_now || []).map((o) => o.user));

  // Top 8 consumers for visual ranking
  const metric = localState.topMetric;
  const topList = allAccounts
    .filter((u) => !SYSTEM_ACCOUNTS.has(u.username) || Number(u[metric] || 0) > 0)
    .slice()
    .sort((a, b) => Number(b[metric] || 0) - Number(a[metric] || 0))
    .slice(0, 8);
  const maxTop = Math.max(1, ...topList.map((u) => Number(u[metric] || 0)));
  const barCls = metric === "gpu_mem_mb" ? "violet" : metric === "cpu_pct" ? "cyan" : "ok";

  // Filtered table rows
  const q = localState.search.trim().toLowerCase();
  let rows = allAccounts.slice();
  if (localState.hideSystem) rows = rows.filter((u) => !SYSTEM_ACCOUNTS.has(u.username));
  if (localState.onlyFlagged) rows = rows.filter((u) => u.heavy || u.gpu_idle_mb > 0 || u.zombies > 0);
  if (q) {
    rows = rows.filter((u) =>
      (u.username || "").toLowerCase().includes(q) ||
      (u.top_procs || []).some((p) => (p.name || "").toLowerCase().includes(q))
    );
  }
  const { key, dir } = localState.sort;
  rows.sort((a, b) => {
    const av = a[key] ?? "";
    const bv = b[key] ?? "";
    if (typeof av === "string") return dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    return dir === "asc" ? Number(av) - Number(bv) : Number(bv) - Number(av);
  });

  root.innerHTML = `
    <section class="card" style="margin-bottom:14px">
      <div class="card-head">
        <div>
          <h2 class="card-title">Top Account tiêu thụ tài nguyên — ${esc(latest.host)}</h2>
          <p class="card-sub">Nhấp vào một tài khoản để mở chi tiết tiến trình &amp; biểu đồ lịch sử</p>
        </div>
        <div class="row-wrap">
          <div class="segmented" id="acc-metric-switch">
            <button type="button" class="seg-btn ${metric === "mem_mb" ? "active" : ""}" data-metric="mem_mb">RAM</button>
            <button type="button" class="seg-btn ${metric === "gpu_mem_mb" ? "active" : ""}" data-metric="gpu_mem_mb">GPU VRAM</button>
            <button type="button" class="seg-btn ${metric === "cpu_pct" ? "active" : ""}" data-metric="cpu_pct">CPU</button>
          </div>
          <a href="/api/hosts/${encodeURIComponent(latest.host)}/export/accounts" class="btn btn-secondary btn-sm">Xuất CSV</a>
        </div>
      </div>

      <div class="grid-2" style="margin-bottom:0">
        ${topList.map((u) => {
          const v = Number(u[metric] || 0);
          const pct = v > 0 ? Math.max(3, (v / maxTop) * 100) : 0;
          const lbl = metric === "cpu_pct" ? `${v.toFixed(1)}%` : fmtMB(v);
          return `
            <div class="item-box" data-jump-acc="${esc(u.username)}" style="cursor:pointer">
              <div class="row-between" style="margin-bottom:5px">
                <span class="mono" style="font-weight:600">${esc(u.username)}
                  ${u.heavy ? `<span class="badge badge-crit">HEAVY</span>` : ""}
                  ${u.gpu_idle_mb > 0 ? `<span class="badge badge-warn">GPU-IDLE</span>` : ""}
                </span>
                <strong class="mono">${lbl}</strong>
              </div>
              <div class="meter"><div class="meter-fill ${barCls}" style="width:${pct}%"></div></div>
            </div>`;
        }).join("")}
      </div>
    </section>

    <section class="card">
      <div class="row-between" style="margin-bottom:12px">
        <div class="row-wrap">
          <input type="search" id="acc-search" class="input input-search" value="${esc(localState.search)}" placeholder="Tìm username hoặc tiến trình (python, java...)">
          <label class="checkbox-pill">
            <input type="checkbox" id="acc-hide-sys" ${localState.hideSystem ? "checked" : ""}>
            <span>Ẩn system accounts</span>
          </label>
          <label class="checkbox-pill">
            <input type="checkbox" id="acc-only-flag" ${localState.onlyFlagged ? "checked" : ""}>
            <span>Chỉ Heavy / GPU-Idle / Zombie</span>
          </label>
        </div>
        <span class="mono-muted">Hiển thị ${rows.length} / ${allAccounts.length} tài khoản</span>
      </div>

      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              ${[
                ["username", "Tài khoản", false],
                ["cpu_pct", "CPU %", true],
                ["mem_mb", "RAM", true],
                ["gpu_mem_mb", "GPU VRAM", true],
                ["procs", "Tiến trình", true],
                ["oldest_proc_age_h", "Tuổi lâu nhất", true],
              ].map(([k, title, num]) => {
                const cls = `sortable ${num ? "num" : ""} ${key === k ? (dir === "asc" ? "sort-asc" : "sort-desc") : ""}`;
                return `<th class="${cls}" data-sort="${k}">${title}</th>`;
              }).join("")}
              <th>Trạng thái</th>
            </tr>
          </thead>
          <tbody>
            ${rows.length === 0
              ? `<tr><td colspan="7" class="muted-sm" style="text-align:center;padding:22px">Không có tài khoản nào khớp bộ lọc.</td></tr>`
              : rows.map((u) => {
                  const exp = localState.expanded.has(u.username);
                  const flags = [];
                  if (onlineUsers.has(u.username)) flags.push(`<span class="badge badge-ok">ONLINE</span>`);
                  if (u.heavy) flags.push(`<span class="badge badge-crit" title="${esc((u.heavy_reasons || []).join("; "))}">HEAVY</span>`);
                  if (u.gpu_idle_mb > 0) flags.push(`<span class="badge badge-warn">GPU-IDLE ${u.gpu_idle_mb}M</span>`);
                  if (u.zombies > 0) flags.push(`<span class="badge badge-warn">${u.zombies} ZOMBIE</span>`);
                  if (!flags.length) flags.push(`<span class="badge badge-muted">Bình thường</span>`);

                  const owner = ownerMap[u.username];
                  const ownerSub = owner
                    ? `<div class="muted-sm" style="margin-top:2px">${esc(owner.group_name || (owner.account_type === "key_member" ? "Key Member" : "Nhóm SV"))}${owner.members?.length ? ` · ${esc(owner.members.map((m) => m.full_name).join(", "))}` : ""}</div>`
                    : "";

                  const mainRow = `
                    <tr class="clickable" data-toggle-acc="${esc(u.username)}">
                      <td class="mono">
                        <div><span class="muted-sm" style="margin-right:6px">${exp ? "▾" : "▸"}</span><strong>${esc(u.username)}</strong></div>
                        ${ownerSub}
                      </td>
                      <td class="num">${Number(u.cpu_pct || 0).toFixed(1)}%</td>
                      <td class="num">${fmtMB(u.mem_mb)}</td>
                      <td class="num">${u.gpu_mem_mb > 0 ? `<strong style="color:var(--violet)">${fmtMB(u.gpu_mem_mb)}</strong>` : "—"}</td>
                      <td class="num">${u.procs}${u.zombies > 0 ? ` <span style="color:var(--warning)">(+${u.zombies}z)</span>` : ""}</td>
                      <td class="num">${fmtUptime(u.oldest_proc_age_h)}</td>
                      <td><div class="row-wrap">${flags.join("")}</div></td>
                    </tr>`;

                  if (!exp) return mainRow;
                  const idleSet = new Set(u.gpu_idle_pids || []);
                  const procs = u.top_procs || [];
                  const canvasId = `acc-hist-${u.username.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
                  return mainRow + `
                    <tr class="drawer-row">
                      <td colspan="7" style="padding:14px">
                        <div class="grid-asymmetric" style="margin-bottom:0">
                          <div>
                            <div class="row-between" style="margin-bottom:8px">
                              <span class="kpi-label">Top tiến trình — ${esc(u.username)}</span>
                              ${u.heavy_reasons?.length ? `<span class="badge badge-crit">${esc(u.heavy_reasons.join(" · "))}</span>` : ""}
                            </div>
                            <table class="proc-subtable">
                              <thead><tr><th>PID</th><th>Tên</th><th class="num">CPU%</th><th class="num">RAM</th><th class="num">VRAM</th><th class="num">Tuổi</th><th>Lệnh</th></tr></thead>
                              <tbody>
                                ${procs.map((p) => {
                                  const isIdle = idleSet.has(p.pid);
                                  return `
                                    <tr class="${isIdle ? "proc-idle" : ""}">
                                      <td>${p.pid}</td>
                                      <td>${esc(p.name)} ${isIdle ? `<span class="badge badge-warn">IDLE</span>` : ""}</td>
                                      <td class="num">${Number(p.cpu_pct || 0).toFixed(1)}%</td>
                                      <td class="num">${fmtMB(p.mem_mb)}</td>
                                      <td class="num">${p.gpu_mem_mb > 0 ? fmtMB(p.gpu_mem_mb) : "—"}</td>
                                      <td class="num">${fmtUptime(p.age_h)}</td>
                                      <td><button type="button" class="btn btn-secondary btn-sm" data-copy-cmd="ps -fp ${p.pid}">ps</button></td>
                                    </tr>`;
                                }).join("")}
                              </tbody>
                            </table>
                          </div>
                          <div>
                            <div class="row-between" style="margin-bottom:6px">
                              <span class="kpi-label">Xu hướng RAM &amp; VRAM (MB)</span>
                              <span class="mono-muted" id="${canvasId}-meta">Đang tải...</span>
                            </div>
                            <div class="chart-wrap chart-sm">
                              <canvas id="${canvasId}" data-acc-hist="${esc(u.username)}"></canvas>
                              <div class="chart-tooltip hidden" id="${canvasId}-tip"></div>
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>`;
                }).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;

  // Bind Controls
  root.querySelectorAll("#acc-metric-switch .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      localState.topMetric = btn.dataset.metric;
      renderAccountsView(root, ctx);
    });
  });

  root.querySelector("#acc-search")?.addEventListener("input", (e) => {
    localState.search = e.target.value;
    renderAccountsView(root, ctx);
    const input = root.querySelector("#acc-search");
    input?.focus();
    input?.setSelectionRange(input.value.length, input.value.length);
  });

  root.querySelector("#acc-hide-sys")?.addEventListener("change", (e) => {
    localState.hideSystem = e.target.checked;
    renderAccountsView(root, ctx);
  });

  root.querySelector("#acc-only-flag")?.addEventListener("change", (e) => {
    localState.onlyFlagged = e.target.checked;
    renderAccountsView(root, ctx);
  });

  root.querySelectorAll("th.sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      localState.sort = {
        key: k,
        dir: localState.sort.key === k && localState.sort.dir === "desc" ? "asc" : "desc",
      };
      renderAccountsView(root, ctx);
    });
  });

  const toggleUser = (user) => {
    if (localState.expanded.has(user)) localState.expanded.delete(user);
    else localState.expanded.add(user);
    if (SYSTEM_ACCOUNTS.has(user)) localState.hideSystem = false;
    renderAccountsView(root, ctx);
  };

  root.querySelectorAll("[data-toggle-acc]").forEach((tr) => {
    tr.addEventListener("click", () => toggleUser(tr.dataset.toggleAcc));
  });
  root.querySelectorAll("[data-jump-acc]").forEach((el) => {
    el.addEventListener("click", () => toggleUser(el.dataset.jumpAcc));
  });
  root.querySelectorAll("[data-copy-cmd]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      copyText(btn.dataset.copyCmd, ctx.toast, `Đã copy: ${btn.dataset.copyCmd}`);
    });
  });

  // Load per-account historical trend charts for expanded rows
  root.querySelectorAll("canvas[data-acc-hist]").forEach(async (canvas) => {
    const user = canvas.dataset.accHist;
    const tip = root.querySelector(`#${canvas.id}-tip`);
    const meta = root.querySelector(`#${canvas.id}-meta`);
    try {
      const res = await api(`/api/hosts/${encodeURIComponent(latest.host)}/accounts/${encodeURIComponent(user)}/history?limit=60`);
      const pts = res.points || [];
      if (meta) meta.textContent = `${pts.length} mẫu`;
      drawLineChart(canvas, tip, pts, [
        { key: "mem_mb", label: "RAM", formatter: fmtMB, color: "#22c55e", dash: [], fill: "rgba(34,197,94,0.14)" },
        { key: "gpu_mem_mb", label: "VRAM", formatter: fmtMB, color: "#a78bfa", dash: [5, 4] },
      ], { autoMax: true, leftPad: 54, yFormatter: (v) => fmtMB(v) });
    } catch {
      if (meta) meta.textContent = "Lỗi tải lịch sử";
    }
  });
}

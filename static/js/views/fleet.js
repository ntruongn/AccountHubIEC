/** View: Fleet Overview — Multi-server aggregate KPIs, host cards with sparklines, and fleet alerts. */
import { drawSparkline } from "../charts.js";
import { esc, fmtAge, fmtMB, fmtUptime, meterTone, severityBadgeClass, statusLabel } from "../format.js";

export function renderFleetView(root, ctx) {
  const fleet = ctx.fleet;
  const hosts = fleet?.hosts || [];
  const t = fleet?.totals || {};
  const alerts = (ctx.alerts || []).filter((a) => !a.acked);

  if (hosts.length === 0) {
    root.innerHTML = `
      <div class="card" style="max-width:660px;margin:36px auto;text-align:center;padding:30px">
        <h2 style="margin:0 0 8px">Chưa có máy chủ nào gửi dữ liệu</h2>
        <p class="muted" style="margin:0 0 16px">Chạy <code>client_bot.py</code> trên máy chủ cần giám sát hoặc nạp file <code>client_bot_report.json</code> để trải nghiệm ngay.</p>
        <pre class="code-block" style="text-align:left;margin-bottom:16px">sudo python3 client_bot.py --server ${esc(window.location.origin)}/api/report --interval 60</pre>
        <div class="row-wrap" style="justify-content:center">
          <button type="button" class="btn btn-primary" id="fleet-import-btn">Import file JSON báo cáo</button>
          <a href="#/settings" class="btn btn-secondary">Hướng dẫn triển khai</a>
        </div>
      </div>`;
    root.querySelector("#fleet-import-btn")?.addEventListener("click", () => ctx.triggerImport());
    return;
  }

  root.innerHTML = `
    <div class="grid-4">
      <article class="kpi-card">
        <div class="row-between">
          <span class="kpi-label">Máy chủ Fleet</span>
          <span class="badge ${t.offline > 0 ? "badge-crit" : t.stale > 0 ? "badge-warn" : "badge-ok"}">${t.online}/${t.hosts} online</span>
        </div>
        <div class="kpi-val">${t.hosts} <span class="muted-sm">nodes</span></div>
        <div class="kpi-foot">
          <span>CPU TB: <strong class="mono">${Number(t.avg_cpu_pct || 0).toFixed(1)}%</strong></span>
          <span>Tổng cores: <strong class="mono">${t.total_cpus || 0}</strong></span>
        </div>
      </article>

      <article class="kpi-card">
        <div class="row-between">
          <span class="kpi-label">Tổng RAM Fleet</span>
          <span class="badge ${t.mem_used_pct >= 85 ? "badge-crit" : t.mem_used_pct >= 70 ? "badge-warn" : "badge-ok"}">${Number(t.mem_used_pct || 0).toFixed(1)}%</span>
        </div>
        <div class="kpi-val">${fmtMB(t.mem_used_mb)}</div>
        <div class="meter"><div class="meter-fill ${meterTone(t.mem_used_pct)}" style="width:${Math.min(100, t.mem_used_pct || 0)}%"></div></div>
        <div class="kpi-foot"><span>Tổng dung lượng: <strong class="mono">${fmtMB(t.mem_total_mb)}</strong></span></div>
      </article>

      <article class="kpi-card">
        <div class="row-between">
          <span class="kpi-label">GPU &amp; VRAM Fleet</span>
          <span class="badge badge-info">${t.total_gpus || 0} GPUs</span>
        </div>
        <div class="kpi-val">${fmtMB(t.vram_used_mb)}</div>
        <div class="meter"><div class="meter-fill violet" style="width:${Math.min(100, t.vram_used_pct || 0)}%"></div></div>
        <div class="kpi-foot"><span>Tổng VRAM: <strong class="mono">${fmtMB(t.vram_total_mb)}</strong></span><span>${Number(t.vram_used_pct || 0).toFixed(1)}%</span></div>
      </article>

      <article class="kpi-card">
        <div class="row-between">
          <span class="kpi-label">Cảnh báo &amp; Phiên</span>
          <span class="badge ${alerts.length > 0 ? "badge-crit" : "badge-ok"}">${alerts.length} active</span>
        </div>
        <div class="kpi-val">${t.online_sessions || 0} <span class="muted-sm">online</span></div>
        <div class="kpi-foot">
          <span>Stale: <strong>${t.stale || 0}</strong> · Offline: <strong>${t.offline || 0}</strong></span>
          <a href="#/alerts" style="color:var(--cyan);text-decoration:none;font-weight:500">Xem cảnh báo →</a>
        </div>
      </article>
    </div>

    <div class="row-between" style="margin-bottom:10px">
      <div>
        <h2 class="card-title">Danh sách Máy chủ (${hosts.length})</h2>
        <p class="card-sub">Nhấp vào từng máy chủ để xem chi tiết GPU, ổ đĩa, tiến trình và lịch sử</p>
      </div>
    </div>

    <div class="grid-3" id="fleet-host-grid">
      ${hosts.map((h, idx) => `
        <article class="host-card" data-open-host="${esc(h.host)}" tabindex="0" role="button">
          <div class="row-between">
            <div class="row-wrap">
              <span class="status-dot status-${esc(h.status)}" aria-hidden="true"></span>
              <strong class="mono" style="font-size:15px">${esc(h.host)}</strong>
              <span class="badge ${h.status === "online" ? "badge-ok" : h.status === "stale" ? "badge-warn" : "badge-crit"}">${statusLabel(h.status)}</span>
            </div>
            ${h.alerts_count > 0
              ? `<span class="badge badge-crit">${h.alerts_count} alerts</span>`
              : `<span class="badge badge-ok">OK</span>`}
          </div>

          <div class="muted-sm mono">${esc((h.os || "").split("-with-")[0])} · ${fmtAge(h.age_seconds)}</div>
          ${h.note ? `<div class="muted-sm" style="color:var(--info-text)">${esc(h.note)}</div>` : ""}

          <div class="host-card-metrics">
            <div>
              <div class="muted-sm">CPU</div>
              <div class="mono" style="font-weight:700">${Number(h.cpu_pct || 0).toFixed(1)}%</div>
              <div class="meter" style="margin-top:4px"><div class="meter-fill cyan" style="width:${Math.min(100, h.cpu_pct || 0)}%"></div></div>
            </div>
            <div>
              <div class="muted-sm">RAM</div>
              <div class="mono" style="font-weight:700">${Number(h.mem_used_pct || 0).toFixed(1)}%</div>
              <div class="meter" style="margin-top:4px"><div class="meter-fill ${meterTone(h.mem_used_pct)}" style="width:${Math.min(100, h.mem_used_pct || 0)}%"></div></div>
            </div>
            <div>
              <div class="muted-sm">Disk max</div>
              <div class="mono" style="font-weight:700">${Number(h.disk_max_pct || 0).toFixed(1)}%</div>
              <div class="meter" style="margin-top:4px"><div class="meter-fill ${meterTone(h.disk_max_pct)}" style="width:${Math.min(100, h.disk_max_pct || 0)}%"></div></div>
            </div>
          </div>

          <div class="spark-box"><canvas id="fleet-spark-${idx}" aria-label="Biểu đồ CPU gần đây của ${esc(h.host)}"></canvas></div>

          <div class="row-between muted-sm mono">
            <span>GPU: ${h.gpu_count} (${Number(h.gpu_mem_pct || 0).toFixed(0)}% VRAM)</span>
            <span>Up: ${fmtUptime(h.uptime_h)}</span>
            <span>Hub: ${esc(h.jupyterhub_service || "?")}</span>
          </div>
        </article>
      `).join("")}
    </div>

    <section class="card">
      <div class="card-head">
        <div>
          <h3 class="card-title">Cảnh báo chưa xác nhận trên toàn hệ thống (${alerts.length})</h3>
          <p class="card-sub">Hiển thị các cảnh báo mới nhất cần xử lý</p>
        </div>
        <a href="#/alerts" class="btn btn-secondary btn-sm">Mở Trung tâm Cảnh báo</a>
      </div>
      ${alerts.length === 0
        ? `<p class="muted-sm" style="margin:0">Tất cả máy chủ đều ổn định, không có cảnh báo cần xử lý.</p>`
        : `<div class="stack">${alerts.slice(0, 8).map((a) => `
            <div class="alert-row sev-${esc(a.severity)}">
              <div class="row-wrap">
                <span class="badge ${severityBadgeClass(a.severity)}">${esc(a.tag)}</span>
                <span class="badge badge-muted">${esc(a.host)}</span>
                <span>${esc(a.message)}</span>
              </div>
              <span class="mono-muted">×${a.occurrences || 1} · ${fmtAge(a.age_seconds)}</span>
            </div>`).join("")}</div>`}
    </section>
  `;

  hosts.forEach((h, idx) => {
    const canvas = root.querySelector(`#fleet-spark-${idx}`);
    const vals = (h.sparkline || []).map((p) => p.cpu_pct);
    drawSparkline(canvas, vals.length ? vals : [h.cpu_pct || 0], "#38bdf8");
  });

  root.querySelectorAll("[data-open-host]").forEach((card) => {
    const open = () => ctx.navigate(`#/host/${encodeURIComponent(card.dataset.openHost)}`);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
  });
}

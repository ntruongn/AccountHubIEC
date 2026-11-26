/** View: Host Deep Dive — Gauges, GPU & Disk telemetry, Time-Series Charts, Snapshot Time-Travel, Raw JSON. */
import { api, apiPost } from "../api.js";
import { drawLineChart, renderGaugeSVG } from "../charts.js";
import { copyText, esc, fmtAge, fmtDateTime, fmtMB, fmtUptime, meterTone, severityBadgeClass } from "../format.js";

export function renderHostView(root, ctx) {
  const latest = ctx.latest;
  if (!latest) {
    root.innerHTML = `<div class="card"><p class="muted">Hãy chọn một máy chủ hoặc import dữ liệu báo cáo.</p></div>`;
    return;
  }

  const rep = latest.report;
  const sys = rep.system || {};
  const gpus = rep.gpus || [];
  const disks = sys.disks || [];
  const alerts = latest.parsed_alerts || [];
  const snapshots = ctx.snapshots || [];

  const cpuPct = Number(sys.cpu_pct || 0);
  const memUsed = Number(sys.mem_used_mb || 0);
  const memTotal = Number(sys.mem_total_mb || 0);
  const memPct = memTotal > 0 ? (memUsed / memTotal) * 100 : 0;

  const gpuUsed = gpus.reduce((s, g) => s + Number(g.mem_used_mb || 0), 0);
  const gpuTotal = gpus.reduce((s, g) => s + Number(g.mem_total_mb || 0), 0);
  const gpuVramPct = gpuTotal > 0 ? (gpuUsed / gpuTotal) * 100 : 0;
  const gpuUtilAvg = gpus.length ? gpus.reduce((s, g) => s + Number(g.util_pct || 0), 0) / gpus.length : 0;
  const gpuMaxTemp = gpus.length ? Math.max(...gpus.map((g) => Number(g.temp_c || 0))) : null;

  const loadStr = (sys.load_avg || []).map((x) => Number(x).toFixed(2)).join(" / ") || "—";
  const svc = sys.jupyterhub_service || "unknown";
  const svcCls = svc === "active" ? "badge-ok" : svc === "unknown" ? "badge-muted" : "badge-crit";
  const online = rep.online_now || [];
  const gpuIdleAccounts = (rep.usage_by_account || []).filter((u) => Number(u.gpu_idle_mb || 0) > 0);
  const xids = rep.gpu_xid_errors || [];

  root.innerHTML = `
    <!-- Host Banner & Time-Travel Bar -->
    <div class="card" style="margin-bottom:14px">
      <div class="row-between">
        <div class="row-wrap">
          <span class="status-dot status-${esc(latest.status)}" aria-hidden="true"></span>
          <strong class="mono" style="font-size:17px">${esc(latest.host)}</strong>
          <span class="badge ${latest.is_snapshot ? "badge-warn" : "badge-ok"}">${latest.is_snapshot ? `SNAPSHOT #${latest.snapshot_id}` : "MỚI NHẤT"}</span>
          <span class="mono-muted">${esc((rep.os || "").split("-with-")[0])}</span>
          <span class="mono-muted">· Uptime ${fmtUptime(sys.uptime_h)}</span>
          <span class="mono-muted">· Nhận lúc ${fmtDateTime(latest.snapshot_received_at)} (${fmtAge(latest.age_seconds)})</span>
        </div>

        <div class="row-wrap">
          <label class="muted-sm" for="snapshot-select">Time-Travel:</label>
          <select id="snapshot-select" class="select mono" style="max-width:250px">
            <option value="">Mới nhất (${latest.reports_count} mẫu)</option>
            ${snapshots.map((s) => `
              <option value="${s.id}" ${String(ctx.snapshotId || "") === String(s.id) ? "selected" : ""}>
                #${s.id} · ${fmtDateTime(s.received_at)} (CPU ${Number(s.cpu_pct || 0).toFixed(0)}%)
              </option>`).join("")}
          </select>
          <button type="button" class="btn btn-secondary btn-sm" id="btn-edit-note">Ghi chú</button>
          <button type="button" class="btn btn-secondary btn-sm" id="btn-toggle-json">Raw JSON</button>
        </div>
      </div>
      ${latest.note ? `<div class="muted-sm" style="margin-top:8px;color:var(--info-text)">Ghi chú máy chủ: ${esc(latest.note)}</div>` : ""}
    </div>

    ${alerts.length > 0 ? `
      <div class="stack" style="margin-bottom:14px">
        ${alerts.map((a) => `
          <div class="alert-row sev-${esc(a.severity)} ${a.acked ? "acked" : ""}">
            <div class="row-wrap">
              <span class="badge ${severityBadgeClass(a.severity)}">${esc(a.tag)}</span>
              <span>${esc(a.message)}</span>
              ${a.acked ? `<span class="badge badge-muted">Đã xác nhận${a.ack_note ? `: ${esc(a.ack_note)}` : ""}</span>` : ""}
            </div>
            <a href="#/alerts" class="mono-muted" style="text-decoration:none;color:var(--cyan)">Xử lý →</a>
          </div>`).join("")}
      </div>` : ""}

    <!-- 4 Radial Gauge KPI Cards -->
    <div class="grid-4">
      <article class="kpi-card">
        <div class="gauge-row">
          <div>
            <div class="kpi-label">CPU (${sys.cpu_count || "?"} cores)</div>
            <div class="kpi-val" style="margin-top:4px">${cpuPct.toFixed(1)}%</div>
            <div class="mono-muted" style="margin-top:4px">Load ${esc(loadStr)}</div>
          </div>
          ${renderGaugeSVG(cpuPct, cpuPct >= 85 ? "--danger" : "--cyan")}
        </div>
      </article>

      <article class="kpi-card">
        <div class="gauge-row">
          <div>
            <div class="kpi-label">Bộ nhớ RAM</div>
            <div class="kpi-val" style="margin-top:4px">${fmtMB(memUsed)}</div>
            <div class="mono-muted" style="margin-top:4px">/ ${fmtMB(memTotal)} · Swap ${fmtMB(sys.swap_used_mb)}</div>
          </div>
          ${renderGaugeSVG(memPct, memPct >= 85 ? "--danger" : memPct >= 70 ? "--warning" : "--accent")}
        </div>
      </article>

      <article class="kpi-card">
        <div class="gauge-row">
          <div>
            <div class="kpi-label">GPU VRAM (${gpus.length} GPU)</div>
            <div class="kpi-val" style="margin-top:4px">${gpus.length ? fmtMB(gpuUsed) : "N/A"}</div>
            <div class="mono-muted" style="margin-top:4px">${gpus.length ? `/ ${fmtMB(gpuTotal)} · Max ${gpuMaxTemp ?? "—"}°C` : "Không có GPU"}</div>
          </div>
          ${renderGaugeSVG(gpuVramPct, "--violet")}
        </div>
      </article>

      <article class="kpi-card">
        <div class="row-between">
          <span class="kpi-label">Dịch vụ &amp; Phiên</span>
          <span class="badge ${svcCls}">Hub: ${esc(svc)}</span>
        </div>
        <div class="kpi-val">${online.length} <span class="muted-sm">online</span></div>
        <div class="kpi-foot">
          <span>Heavy: <strong>${(rep.heavy_accounts || []).length}</strong></span>
          <span>Failed login: <strong>${rep.failed_logins_recent ?? "N/A"}</strong></span>
        </div>
      </article>
    </div>

    <!-- Time-Series Section -->
    <div class="row-between" style="margin:6px 0 10px">
      <div>
        <h2 class="card-title">Lịch sử hiệu năng theo thời gian</h2>
        <p class="card-sub">${ctx.history.length} điểm dữ liệu trong ${ctx.historyHours}h gần nhất</p>
      </div>
      <div class="segmented" id="host-range-switch">
        ${[1, 6, 24, 168].map((h) => `
          <button type="button" class="seg-btn ${ctx.historyHours === h ? "active" : ""}" data-hours="${h}">${h === 168 ? "7d" : `${h}h`}</button>
        `).join("")}
      </div>
    </div>

    <div class="grid-2">
      <article class="card">
        <div class="card-head">
          <div>
            <h3 class="card-title">CPU % &amp; RAM %</h3>
            <div class="chart-legend">
              <span class="legend-item"><span class="legend-line" style="color:var(--cyan)"></span>CPU %</span>
              <span class="legend-item"><span class="legend-line dashed" style="color:var(--accent)"></span>RAM %</span>
            </div>
          </div>
          <span class="mono-muted">Load 1m: ${(sys.load_avg?.[0] ?? 0).toFixed(2)}</span>
        </div>
        <div class="chart-wrap">
          <canvas id="host-chart-sys"></canvas>
          <div class="chart-tooltip hidden" id="host-tip-sys"></div>
        </div>
      </article>

      <article class="card">
        <div class="card-head">
          <div>
            <h3 class="card-title">GPU Util %, VRAM % &amp; Nhiệt độ</h3>
            <div class="chart-legend">
              <span class="legend-item"><span class="legend-line" style="color:var(--violet)"></span>Util %</span>
              <span class="legend-item"><span class="legend-line dashed" style="color:var(--warning)"></span>VRAM %</span>
              <span class="legend-item"><span class="legend-line dotted" style="color:#fb7185"></span>Temp °C</span>
            </div>
          </div>
          <span class="mono-muted">Avg Util ${gpuUtilAvg.toFixed(0)}%</span>
        </div>
        <div class="chart-wrap">
          <canvas id="host-chart-gpu"></canvas>
          <div class="chart-tooltip hidden" id="host-tip-gpu"></div>
        </div>
      </article>
    </div>

    <!-- GPU Cards & Disks -->
    <div class="grid-2">
      <article class="card">
        <div class="card-head">
          <h3 class="card-title">Phần cứng GPU &amp; Xid Errors</h3>
          <span class="mono-muted">${gpus.length} thiết bị</span>
        </div>
        <div class="stack">
          ${gpus.length === 0 ? `<p class="muted-sm" style="margin:0">Không phát hiện GPU NVIDIA.</p>` : gpus.map((g) => {
            const util = Number(g.util_pct || 0);
            const vramP = g.mem_total_mb ? (Number(g.mem_used_mb || 0) / Number(g.mem_total_mb)) * 100 : 0;
            const pwrP = g.power_limit_w ? (Number(g.power_w || 0) / Number(g.power_limit_w)) * 100 : 0;
            const temp = Number(g.temp_c || 0);
            return `
              <div class="item-box stack" style="gap:9px">
                <div class="row-between">
                  <div class="row-wrap"><span class="badge badge-info">GPU ${g.index}</span><strong>${esc(g.name)}</strong></div>
                  <span class="badge ${temp >= 85 ? "badge-crit" : temp >= 72 ? "badge-warn" : "badge-ok"}">${temp.toFixed(0)}°C</span>
                </div>
                <div class="host-card-metrics">
                  <div>
                    <div class="row-between muted-sm"><span>Util</span><strong class="mono">${util.toFixed(0)}%</strong></div>
                    <div class="meter" style="margin-top:4px"><div class="meter-fill violet" style="width:${Math.min(100, util)}%"></div></div>
                  </div>
                  <div>
                    <div class="row-between muted-sm"><span>VRAM</span><strong class="mono">${Math.round(g.mem_used_mb || 0)}/${Math.round(g.mem_total_mb || 0)}M</strong></div>
                    <div class="meter" style="margin-top:4px"><div class="meter-fill ${meterTone(vramP)}" style="width:${Math.min(100, vramP)}%"></div></div>
                  </div>
                  <div>
                    <div class="row-between muted-sm"><span>Power</span><strong class="mono">${Number(g.power_w || 0).toFixed(1)}/${Number(g.power_limit_w || 0).toFixed(0)}W</strong></div>
                    <div class="meter" style="margin-top:4px"><div class="meter-fill cyan" style="width:${Math.min(100, pwrP)}%"></div></div>
                  </div>
                </div>
              </div>`;
          }).join("")}
          ${xids.length > 0
            ? `<div class="alert-row sev-critical"><span class="badge badge-crit">GPU XID</span><span class="mono" style="font-size:11.5px">${esc(xids.join(" | "))}</span></div>`
            : `<div class="mono-muted">dmesg GPU Xid errors: 0 (không ghi nhận lỗi phần cứng)</div>`}
        </div>
      </article>

      <article class="card">
        <div class="card-head">
          <h3 class="card-title">Phân vùng Ổ đĩa (Disk Partitions)</h3>
          <span class="mono-muted">${disks.length} phân vùng</span>
        </div>
        <div class="stack">
          ${disks.length === 0 ? `<p class="muted-sm" style="margin:0">Không có dữ liệu ổ đĩa.</p>` : disks.map((d) => {
            const pct = Number(d.used_pct || 0);
            const tone = meterTone(pct);
            return `
              <div class="item-box">
                <div class="row-between" style="margin-bottom:6px">
                  <strong class="mono">${esc(d.mount)}</strong>
                  <div class="row-wrap">
                    <span class="mono-muted">${Number(d.total_gb || 0).toFixed(1)} GB</span>
                    <span class="badge ${pct >= 90 ? "badge-crit" : pct >= 75 ? "badge-warn" : "badge-ok"}">${pct.toFixed(1)}%</span>
                  </div>
                </div>
                <div class="disk-track" title="Mốc cảnh báo 90%">
                  <div class="disk-threshold" style="left:90%"></div>
                  <div class="meter-fill ${tone}" style="width:${Math.min(100, pct)}%"></div>
                </div>
              </div>`;
          }).join("")}
        </div>
      </article>
    </div>

    <!-- Online Sessions & GPU-Idle -->
    <div class="grid-2">
      <article class="card">
        <div class="card-head">
          <h3 class="card-title">Phiên đang Online (who)</h3>
          <a href="#/users" class="mono-muted" style="color:var(--cyan);text-decoration:none">Xem tất cả users →</a>
        </div>
        <div class="stack">
          ${online.length === 0 ? `<p class="muted-sm" style="margin:0">Không có phiên SSH/TTY nào đang mở.</p>` : online.map((s) => `
            <div class="item-box row-between">
              <div><strong class="mono">${esc(s.user)}</strong> <span class="mono-muted">· ${esc(s.terminal || "tty")} ${s.host ? `(${esc(s.host)})` : ""}</span></div>
              <span class="badge badge-ok">từ ${fmtDateTime(s.since)}</span>
            </div>`).join("")}
        </div>
      </article>

      <article class="card">
        <div class="card-head">
          <h3 class="card-title">Nghi bỏ quên VRAM (GPU-IDLE)</h3>
          <a href="#/accounts" class="mono-muted" style="color:var(--cyan);text-decoration:none">Xem tiến trình →</a>
        </div>
        <div class="stack">
          ${gpuIdleAccounts.length === 0
            ? `<p class="muted-sm" style="margin:0">Không có tiến trình nào giữ VRAM mà CPU ~0%.</p>`
            : gpuIdleAccounts.map((u) => `
              <div class="alert-row sev-warning">
                <div class="row-wrap">
                  <span class="badge badge-warn">GPU-IDLE</span>
                  <strong class="mono">${esc(u.username)}</strong>
                  <span class="mono-muted">${u.gpu_idle_mb} MB · PID ${esc((u.gpu_idle_pids || []).join(", "))}</span>
                </div>
                <button type="button" class="btn btn-secondary btn-sm" data-copy-ps="${esc((u.gpu_idle_pids || []).join(","))}">Copy ps</button>
              </div>`).join("")}
        </div>
      </article>
    </div>

    <!-- Collapsible Raw JSON -->
    <section class="card hidden" id="host-raw-card">
      <div class="card-head">
        <div>
          <h3 class="card-title">Payload JSON gốc</h3>
          <p class="card-sub">Host: ${esc(latest.host)} · Timestamp: ${esc(rep.timestamp)}</p>
        </div>
        <div class="row-wrap">
          <button type="button" class="btn btn-secondary btn-sm" id="btn-copy-raw">Sao chép</button>
          <button type="button" class="btn btn-secondary btn-sm" id="btn-dl-raw">Tải .json</button>
        </div>
      </div>
      <pre class="code-block" id="host-raw-pre"></pre>
    </section>
  `;

  // Draw Charts
  drawLineChart(root.querySelector("#host-chart-sys"), root.querySelector("#host-tip-sys"), ctx.history, [
    { key: "cpu_pct", label: "CPU", unit: "%", color: "#38bdf8", dash: [], fill: "rgba(56,189,248,0.14)" },
    { key: "mem_used_pct", label: "RAM", unit: "%", color: "#22c55e", dash: [6, 4] },
  ], { yMax: 100 });

  drawLineChart(root.querySelector("#host-chart-gpu"), root.querySelector("#host-tip-gpu"), ctx.history, [
    { key: "gpu_util_avg", label: "GPU Util", unit: "%", color: "#a78bfa", dash: [], fill: "rgba(167,139,250,0.14)" },
    { key: "gpu_mem_pct", label: "VRAM", unit: "%", color: "#f59e0b", dash: [6, 4] },
    { key: "gpu_temp_max", label: "Temp", unit: "°C", color: "#fb7185", dash: [2, 3] },
  ], { yMax: 100 });

  // Events
  root.querySelector("#snapshot-select")?.addEventListener("change", (e) => {
    const val = e.target.value ? Number(e.target.value) : null;
    ctx.setSnapshot(val);
  });

  root.querySelectorAll("#host-range-switch .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => ctx.setHistoryHours(Number(btn.dataset.hours)));
  });

  root.querySelector("#btn-edit-note")?.addEventListener("click", async () => {
    const next = window.prompt(`Nhập ghi chú cho máy chủ "${latest.host}" (ví dụ: Lab AI - Tầng 4):`, latest.note || "");
    if (next === null) return;
    try {
      await apiPost(`/api/hosts/${encodeURIComponent(latest.host)}/note`, { note: next });
      ctx.toast("Đã lưu ghi chú máy chủ", "ok");
      ctx.refresh();
    } catch (err) {
      ctx.toast(`Lỗi lưu ghi chú: ${err.message}`, "err");
    }
  });

  root.querySelector("#btn-toggle-json")?.addEventListener("click", () => {
    const card = root.querySelector("#host-raw-card");
    const pre = root.querySelector("#host-raw-pre");
    const opening = card.classList.contains("hidden");
    card.classList.toggle("hidden", !opening);
    if (opening) {
      pre.textContent = JSON.stringify(rep, null, 2);
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });

  root.querySelector("#btn-copy-raw")?.addEventListener("click", () => copyText(JSON.stringify(rep, null, 2), ctx.toast));
  root.querySelector("#btn-dl-raw")?.addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(rep, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${latest.host}_report.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });

  root.querySelectorAll("[data-copy-ps]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const pids = btn.dataset.copyPs;
      copyText(`ps -fp ${pids}`, ctx.toast, `Đã copy: ps -fp ${pids}`);
    });
  });
}

/** View: Alert Center & Incident Timeline — Active alerts with Ack/Mute & notes + historical incident log. */
import { api, apiPost } from "../api.js";
import { esc, fmtAge, fmtDateTime, severityBadgeClass } from "../format.js";

const localState = {
  subtab: "active", // active | timeline
  sev: "all",
  hostOnly: false,
  hideAcked: false,
  events: null,
};

export async function renderAlertsView(root, ctx) {
  const alerts = ctx.alerts || [];
  let filtered = alerts.slice();
  if (localState.sev !== "all") filtered = filtered.filter((a) => a.severity === localState.sev);
  if (localState.hostOnly && ctx.selectedHost) filtered = filtered.filter((a) => a.host === ctx.selectedHost);
  if (localState.hideAcked) filtered = filtered.filter((a) => !a.acked);

  root.innerHTML = `
    <div class="card">
      <div class="row-between" style="margin-bottom:14px">
        <div class="segmented" id="alert-subtabs">
          <button type="button" class="seg-btn ${localState.subtab === "active" ? "active" : ""}" data-sub="active">Đang hoạt động (${alerts.length})</button>
          <button type="button" class="seg-btn ${localState.subtab === "timeline" ? "active" : ""}" data-sub="timeline">Lịch sử sự cố (Timeline)</button>
        </div>

        <div class="row-wrap">
          <div class="segmented" id="alert-sev-btns">
            ${["all", "critical", "warning", "info"].map((s) => `
              <button type="button" class="seg-btn ${localState.sev === s ? "active" : ""}" data-sev="${s}">
                ${s === "all" ? "Tất cả" : s.toUpperCase()}
              </button>`).join("")}
          </div>
          <label class="checkbox-pill">
            <input type="checkbox" id="al-host-only" ${localState.hostOnly ? "checked" : ""}>
            <span>Chỉ host ${esc(ctx.selectedHost || "")}</span>
          </label>
          ${localState.subtab === "active" ? `
            <label class="checkbox-pill">
              <input type="checkbox" id="al-hide-ack" ${localState.hideAcked ? "checked" : ""}>
              <span>Ẩn đã xác nhận</span>
            </label>` : ""}
        </div>
      </div>

      <div id="alert-view-body"></div>
    </div>
  `;

  const body = root.querySelector("#alert-view-body");

  if (localState.subtab === "active") {
    body.innerHTML = filtered.length === 0
      ? `<p class="muted-sm" style="padding:14px 4px;margin:0">Không có cảnh báo nào khớp bộ lọc.</p>`
      : `<div class="stack">${filtered.map((a) => `
          <div class="alert-row sev-${esc(a.severity)} ${a.acked ? "acked" : ""}">
            <div style="flex:1;min-width:240px">
              <div class="row-wrap" style="margin-bottom:4px">
                <span class="badge ${severityBadgeClass(a.severity)}">${esc(a.tag)}</span>
                <span class="badge badge-muted">${esc(a.host)}</span>
                <span class="mono-muted">Mở lúc: ${fmtDateTime(a.opened_at)} · ×${a.occurrences || 1} lần</span>
                ${a.acked ? `<span class="badge badge-ok">Đã xác nhận</span>` : ""}
              </div>
              <div>${esc(a.message)}</div>
              ${a.ack_note ? `<div class="muted-sm" style="margin-top:4px">Ghi chú xử lý: <strong>${esc(a.ack_note)}</strong> (${fmtDateTime(a.acked_at)})</div>` : ""}
            </div>
            <div class="row-wrap">
              <span class="mono-muted">${fmtAge(a.age_seconds)}</span>
              <button type="button" class="btn btn-secondary btn-sm"
                data-ack-host="${esc(a.host)}"
                data-ack-fp="${esc(a.fingerprint)}"
                data-ack-cur="${a.acked ? "1" : "0"}">
                ${a.acked ? "Bỏ xác nhận" : "Xác nhận (Ack)"}
              </button>
            </div>
          </div>`).join("")}</div>`;

    body.querySelectorAll("[data-ack-fp]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const host = btn.dataset.ackHost;
        const fp = btn.dataset.ackFp;
        const currentlyAcked = btn.dataset.ackCur === "1";
        let note = "";
        if (!currentlyAcked) {
          const input = window.prompt("Ghi chú xử lý (tùy chọn, ví dụ: Đã nhắn sinh viên tắt kernel):", "");
          if (input === null) return;
          note = input;
        }
        try {
          await apiPost("/api/alerts/ack", { host, fingerprint: fp, acked: !currentlyAcked, note });
          ctx.toast(currentlyAcked ? "Đã mở lại cảnh báo" : "Đã xác nhận cảnh báo", "ok");
          ctx.refresh();
        } catch (err) {
          ctx.toast(`Lỗi: ${err.message}`, "err");
        }
      });
    });
  } else {
    body.innerHTML = `<p class="muted-sm">Đang tải lịch sử sự cố...</p>`;
    try {
      const qs = localState.hostOnly && ctx.selectedHost ? `?host=${encodeURIComponent(ctx.selectedHost)}&limit=150` : "?limit=150";
      const res = await api(`/api/alerts/events${qs}`);
      let events = res.events || [];
      if (localState.sev !== "all") events = events.filter((e) => e.severity === localState.sev);
      body.innerHTML = events.length === 0
        ? `<p class="muted-sm">Chưa có sự cố nào được ghi nhận.</p>`
        : `<div class="table-scroll"><table class="data-table">
            <thead>
              <tr><th>Trạng thái</th><th>Host</th><th>Loại</th><th>Nội dung</th><th class="num">Số lần</th><th>Bắt đầu</th><th>Kết thúc</th></tr>
            </thead>
            <tbody>
              ${events.map((ev) => `
                <tr>
                  <td>${ev.resolved_at ? `<span class="badge badge-ok">Đã hết</span>` : `<span class="badge badge-crit">Đang diễn ra</span>`}</td>
                  <td class="mono">${esc(ev.host)}</td>
                  <td><span class="badge ${severityBadgeClass(ev.severity)}">${esc(ev.tag)}</span></td>
                  <td>${esc(ev.message)}</td>
                  <td class="num">${ev.occurrences}</td>
                  <td class="mono-muted">${fmtDateTime(ev.opened_at)}</td>
                  <td class="mono-muted">${ev.resolved_at ? fmtDateTime(ev.resolved_at) : "—"}</td>
                </tr>`).join("")}
            </tbody>
          </table></div>`;
    } catch (err) {
      body.innerHTML = `<p class="field-error">Lỗi tải lịch sử: ${esc(err.message)}</p>`;
    }
  }

  // Bind filters
  root.querySelectorAll("#alert-subtabs .seg-btn").forEach((b) => {
    b.addEventListener("click", () => { localState.subtab = b.dataset.sub; renderAlertsView(root, ctx); });
  });
  root.querySelectorAll("#alert-sev-btns .seg-btn").forEach((b) => {
    b.addEventListener("click", () => { localState.sev = b.dataset.sev; renderAlertsView(root, ctx); });
  });
  root.querySelector("#al-host-only")?.addEventListener("change", (e) => {
    localState.hostOnly = e.target.checked;
    renderAlertsView(root, ctx);
  });
  root.querySelector("#al-hide-ack")?.addEventListener("change", (e) => {
    localState.hideAcked = e.target.checked;
    renderAlertsView(root, ctx);
  });
}

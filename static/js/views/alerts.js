/** View: Alert Center, Linked Account Owner Actions (Send Email), Incident Timeline & Email Delivery Log. */
import { api, apiPost } from "../api.js";
import { openEmailModal } from "../email_modal.js";
import { esc, fmtAge, fmtDateTime, severityBadgeClass } from "../format.js";

const localState = {
  subtab: "active", // active | timeline | emails
  sev: "all",
  hostOnly: false,
  hideAcked: false,
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
          <button type="button" class="seg-btn ${localState.subtab === "timeline" ? "active" : ""}" data-sub="timeline">Lịch sử sự cố</button>
          <button type="button" class="seg-btn ${localState.subtab === "emails" ? "active" : ""}" data-sub="emails">Lịch sử Email đã gửi</button>
        </div>

        ${localState.subtab !== "emails" ? `
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
          </div>` : ""}
      </div>

      <div id="alert-view-body"></div>
    </div>
    <div id="alert-modal-host"></div>
  `;

  const body = root.querySelector("#alert-view-body");

  if (localState.subtab === "active") {
    body.innerHTML = filtered.length === 0
      ? `<p class="muted-sm" style="padding:14px 4px;margin:0">Không có cảnh báo nào khớp bộ lọc.</p>`
      : `<div class="stack">${filtered.map((a, idx) => {
          const owner = a.owner;
          let ownerLine = "";
          if (owner) {
            const memSummary = (owner.members || [])
              .map((m) => `${m.full_name}${m.email ? ` <${m.email}>` : ""}${m.phone ? ` (${m.phone})` : ""}`)
              .join("; ");
            ownerLine = `
              <div class="muted-sm" style="margin-top:5px;color:var(--info-text)">
                Chủ tài khoản: <strong>${esc(owner.group_name || owner.account_type)}</strong>
                ${owner.advisor ? ` · GVHD: ${esc(owner.advisor)}` : ""}
                ${memSummary ? ` · ${esc(memSummary)}` : ""}
              </div>`;
          } else if (a.target_username) {
            ownerLine = `
              <div class="muted-sm" style="margin-top:5px">
                Tài khoản <code>${esc(a.target_username)}</code> chưa có email liên hệ — <a href="#/registry" style="color:var(--cyan);text-decoration:none">Gán chủ tài khoản →</a>
              </div>`;
          }

          return `
            <div class="alert-row sev-${esc(a.severity)} ${a.acked ? "acked" : ""}">
              <div style="flex:1;min-width:260px">
                <div class="row-wrap" style="margin-bottom:4px">
                  <span class="badge ${severityBadgeClass(a.severity)}">${esc(a.tag)}</span>
                  <span class="badge badge-muted">${esc(a.host)}</span>
                  <span class="mono-muted">Mở lúc: ${fmtDateTime(a.opened_at)} · ×${a.occurrences || 1} lần</span>
                  ${a.acked ? `<span class="badge badge-ok">Đã xác nhận</span>` : ""}
                </div>
                <div>${esc(a.message)}</div>
                ${ownerLine}
                ${a.ack_note ? `<div class="muted-sm" style="margin-top:4px">Ghi chú xử lý: <strong>${esc(a.ack_note)}</strong> (${fmtDateTime(a.acked_at)})</div>` : ""}
              </div>
              <div class="row-wrap">
                <span class="mono-muted">${fmtAge(a.age_seconds)}</span>
                <button type="button" class="btn btn-primary btn-sm" data-mail-idx="${idx}">
                  Gửi email${owner?.emails?.length ? ` (${owner.emails.length})` : ""}
                </button>
                <button type="button" class="btn btn-secondary btn-sm"
                  data-ack-host="${esc(a.host)}"
                  data-ack-fp="${esc(a.fingerprint)}"
                  data-ack-cur="${a.acked ? "1" : "0"}">
                  ${a.acked ? "Bỏ xác nhận" : "Xác nhận (Ack)"}
                </button>
              </div>
            </div>`;
        }).join("")}</div>`;

    body.querySelectorAll("[data-mail-idx]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const a = filtered[Number(btn.dataset.mailIdx)];
        if (!a) return;
        openEmailModal(ctx.modalRoot || root.querySelector("#alert-modal-host"), { host: a.host, alert: a, owner: a.owner }, ctx);
      });
    });

    body.querySelectorAll("[data-ack-fp]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const host = btn.dataset.ackHost;
        const fp = btn.dataset.ackFp;
        const currentlyAcked = btn.dataset.ackCur === "1";
        let note = "";
        if (!currentlyAcked) {
          const input = window.prompt("Ghi chú xử lý (tùy chọn, ví dụ: Đã gửi mail nhắc sinh viên tắt kernel):", "");
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
  } else if (localState.subtab === "timeline") {
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
  } else {
    body.innerHTML = `<p class="muted-sm">Đang tải lịch sử email...</p>`;
    try {
      const res = await api("/api/email/logs?limit=100");
      const logs = res.logs || [];
      body.innerHTML = logs.length === 0
        ? `<p class="muted-sm">Chưa có email cảnh báo nào được gửi.</p>`
        : `<div class="table-scroll"><table class="data-table">
            <thead>
              <tr><th>Trạng thái</th><th>Chế độ</th><th>Host / User</th><th>Người nhận</th><th>Tiêu đề</th><th>Thời gian</th></tr>
            </thead>
            <tbody>
              ${logs.map((l) => `
                <tr>
                  <td><span class="badge ${l.status === "sent" ? "badge-ok" : "badge-crit"}">${l.status === "sent" ? "Đã gửi" : "Lỗi"}</span></td>
                  <td><span class="badge badge-muted">${esc(l.mode)}</span></td>
                  <td class="mono">${esc([l.host, l.username].filter(Boolean).join(" / ") || "—")}</td>
                  <td class="mono-muted">${esc(l.recipients)}${l.cc ? `<div style="font-size:11px">CC: ${esc(l.cc)}</div>` : ""}</td>
                  <td>${esc(l.subject)}${l.error ? `<div class="field-error" style="margin:2px 0 0">${esc(l.error)}</div>` : ""}</td>
                  <td class="mono-muted">${fmtDateTime(l.sent_at)}</td>
                </tr>`).join("")}
            </tbody>
          </table></div>`;
    } catch (err) {
      body.innerHTML = `<p class="field-error">Lỗi tải lịch sử email: ${esc(err.message)}</p>`;
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

/** Reusable Email Compose & Action-Request Modal (supports SMTP send, Copy for Zalo/Teams, and mailto fallback). */
import { apiPost } from "./api.js";
import { copyText, esc } from "./format.js";

export async function openEmailModal(hostEl, opts, ctx) {
  let res;
  try {
    res = await apiPost("/api/email/draft", opts);
  } catch (err) {
    ctx.toast(`Không tạo được mẫu email: ${err.message}`, "err");
    return;
  }

  const draft = res.draft || {};
  const owner = res.owner || opts.owner || null;
  const smtpReady = Boolean(res.smtp_configured);
  const toInit = (draft.to || []).join(", ");
  const ccInit = res.default_cc || "";
  const isAccountMode = Boolean(opts.account_id);

  hostEl.innerHTML = `
    <div class="modal-backdrop" id="mail-modal-bg" role="dialog" aria-modal="true">
      <form class="modal-card modal-lg" id="mail-modal-form">
        <div class="card-head">
          <div>
            <h3 class="card-title">Gửi Email Cảnh báo &amp; Yêu cầu xử lý</h3>
            <p class="card-sub">
              ${owner
                ? `Chủ tài khoản: <strong>${esc(owner.group_name || owner.username || "")}</strong>${owner.advisor ? ` · GVHD: ${esc(owner.advisor)}` : ""}`
                : "Chưa liên kết thông tin chủ tài khoản — bạn có thể nhập trực tiếp email người nhận"}
            </p>
          </div>
          <button type="button" class="icon-btn" id="mail-close" aria-label="Đóng">✕</button>
        </div>

        ${!smtpReady ? `
          <div class="alert-row sev-warning" style="margin-bottom:12px">
            <div class="row-wrap">
              <span class="badge badge-warn">SMTP CHƯA BẬT</span>
              <span class="muted-sm" style="color:var(--fg)">Bật mục <code>"smtp"</code> trong <code>server_config.json</code> để gửi tự động, hoặc dùng nút <strong>Copy nội dung</strong> / <strong>Mở ứng dụng Mail</strong> bên dưới.</span>
            </div>
          </div>` : ""}

        ${owner?.members?.length ? `
          <div class="item-box" style="margin-bottom:12px">
            <div class="kpi-label" style="margin-bottom:6px">Thành viên liên kết (${owner.members.length})</div>
            <div class="row-wrap">
              ${owner.members.map((m) => `
                <span class="badge badge-info" title="${esc([m.email, m.phone].filter(Boolean).join(" · "))}">
                  ${esc(m.full_name)}${m.student_id ? ` (${esc(m.student_id)})` : ""}${m.phone ? ` · ${esc(m.phone)}` : ""}
                </span>`).join("")}
            </div>
          </div>` : ""}

        ${isAccountMode ? `
          <div class="row-wrap" style="margin-bottom:10px">
            <span class="muted-sm">Mẫu nhanh:</span>
            ${[["general", "Thông báo chung"], ["expiry", "Nhắc gia hạn"], ["cleanup", "Yêu cầu dọn ổ đĩa"]].map(([k, lbl]) => `
              <button type="button" class="btn btn-sm ${(opts.kind || "general") === k ? "btn-primary" : "btn-secondary"}" data-mail-kind="${k}">${lbl}</button>
            `).join("")}
          </div>` : ""}

        <div class="grid-2" style="margin-bottom:10px">
          <div class="field" style="margin-bottom:0">
            <label for="mail-to">Người nhận (To) * — cách nhau bởi dấu phẩy</label>
            <input id="mail-to" class="input mono" value="${esc(toInit)}" required placeholder="sv1@uni.edu.vn, sv2@uni.edu.vn">
          </div>
          <div class="field" style="margin-bottom:0">
            <label for="mail-cc">CC (GVHD / Quản trị)</label>
            <input id="mail-cc" class="input mono" value="${esc(ccInit)}" placeholder="admin@uni.edu.vn">
          </div>
        </div>

        <div class="field">
          <label for="mail-subject">Tiêu đề email *</label>
          <input id="mail-subject" class="input" value="${esc(draft.subject || "")}" required>
        </div>

        <div class="field">
          <label for="mail-body">Nội dung cảnh báo &amp; yêu cầu xử lý *</label>
          <textarea id="mail-body" class="input" rows="12" required>${esc(draft.body_text || "")}</textarea>
        </div>

        <div class="row-between">
          <div class="row-wrap">
            <button type="button" class="btn btn-secondary btn-sm" id="mail-copy-btn">Copy nội dung (Zalo/Teams)</button>
            <button type="button" class="btn btn-secondary btn-sm" id="mail-mailto-btn">Mở ứng dụng Mail</button>
          </div>
          <div class="row-wrap">
            <button type="button" class="btn btn-secondary" id="mail-cancel">Hủy</button>
            <button type="submit" class="btn btn-primary" id="mail-send-btn">${smtpReady ? "Gửi Email ngay" : "Thử gửi qua SMTP"}</button>
          </div>
        </div>
      </form>
    </div>
  `;

  const close = () => { hostEl.innerHTML = ""; };
  hostEl.querySelector("#mail-close")?.addEventListener("click", close);
  hostEl.querySelector("#mail-cancel")?.addEventListener("click", close);
  hostEl.querySelector("#mail-modal-bg")?.addEventListener("click", (e) => { if (e.target.id === "mail-modal-bg") close(); });

  hostEl.querySelectorAll("[data-mail-kind]").forEach((btn) => {
    btn.addEventListener("click", () => openEmailModal(hostEl, { ...opts, kind: btn.dataset.mailKind }, ctx));
  });

  hostEl.querySelector("#mail-copy-btn")?.addEventListener("click", () => {
    const subj = hostEl.querySelector("#mail-subject").value;
    const body = hostEl.querySelector("#mail-body").value;
    copyText(`${subj}\n\n${body}`, ctx.toast, "Đã sao chép nội dung cảnh báo");
  });

  hostEl.querySelector("#mail-mailto-btn")?.addEventListener("click", () => {
    const to = encodeURIComponent(hostEl.querySelector("#mail-to").value.trim());
    const cc = encodeURIComponent(hostEl.querySelector("#mail-cc").value.trim());
    const subj = encodeURIComponent(hostEl.querySelector("#mail-subject").value);
    const body = encodeURIComponent(hostEl.querySelector("#mail-body").value);
    window.location.href = `mailto:${to}?subject=${subj}${cc ? `&cc=${cc}` : ""}&body=${body}`;
  });

  hostEl.querySelector("#mail-modal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = hostEl.querySelector("#mail-send-btn");
    btn.disabled = true;
    btn.textContent = "Đang gửi...";
    try {
      const sendRes = await apiPost("/api/email/send", {
        to: hostEl.querySelector("#mail-to").value,
        cc: hostEl.querySelector("#mail-cc").value,
        subject: hostEl.querySelector("#mail-subject").value,
        body_text: hostEl.querySelector("#mail-body").value,
        host: opts.host || owner?.host || "",
        username: opts.alert?.target_username || owner?.username || "",
        alert_tag: opts.alert?.tag || (opts.kind || "").toUpperCase(),
        fingerprint: opts.alert?.fingerprint || "",
        account_id: opts.account_id || owner?.id || null,
      });
      ctx.toast(sendRes.detail || "Đã gửi email thành công", "ok");
      close();
    } catch (err) {
      ctx.toast(`Gửi email thất bại: ${err.message}`, "err");
      btn.disabled = false;
      btn.textContent = smtpReady ? "Gửi Email ngay" : "Thử gửi qua SMTP";
    }
  });
}

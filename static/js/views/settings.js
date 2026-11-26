/** View: Settings, Client Onboarding Generator, Drag-Drop Import, Webhooks & Host Management. */
import { api, apiDelete, apiPost, setBearerToken } from "../api.js";
import { copyText, esc, fmtAge, statusLabel } from "../format.js";

export async function renderSettingsView(root, ctx) {
  let settings = {};
  let meta = { bot_auth_enabled: false, ui_auth_enabled: false };
  try {
    const res = await api("/api/settings");
    settings = res.settings || {};
    meta = res;
  } catch (err) {
    root.innerHTML = `<div class="card"><p class="field-error">Không tải được cấu hình: ${esc(err.message)}</p></div>`;
    return;
  }

  const origin = window.location.origin;
  const hosts = ctx.fleet?.hosts || [];

  const buildConfigJson = (tokenVal, intervalVal) => JSON.stringify({
    server: `${origin}/api/report`,
    tls: {
      ca_cert: meta.tls_enabled ? "../certs/ca.crt" : "",
      client_cert: meta.mtls_enabled ? "../certs/client.crt" : "",
      client_key: meta.mtls_enabled ? "../certs/client.key" : "",
      insecure: false,
    },
    security: {
      token: tokenVal || "CHANGE_ME_STRONG_SECRET_TOKEN",
      use_hmac: true,
    },
    schedule: {
      interval: Number(intervalVal || 60),
      workers: 6,
      disk_workers: 3,
      disk_every: 3600,
    },
  }, null, 2);

  const buildSystemd = () => `[Unit]
Description=AccountHubIEC Multi-Task Resource Monitor Client
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/accounthub
ExecStart=/usr/bin/python3 /opt/accounthub/client/run.py --config /opt/accounthub/client/config.json
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target`;

  root.innerHTML = `
    <div class="grid-2">
      <!-- Client Onboarding Generator -->
      <section class="card">
        <div class="card-head">
          <div>
            <h2 class="card-title">Kết nối máy chủ mới (client/config.json)</h2>
            <p class="card-sub">Đưa toàn bộ bí mật &amp; chứng chỉ vào file config (chmod 600), không lộ qua argv / ps aux</p>
          </div>
          <div class="row-wrap">
            <span class="badge ${meta.tls_enabled ? "badge-ok" : "badge-warn"}">TLS: ${meta.tls_enabled ? "HTTPS" : "HTTP"}</span>
            ${meta.mtls_enabled ? `<span class="badge badge-ok">mTLS: BẬT</span>` : ""}
            <span class="badge ${meta.hmac_required ? "badge-ok" : meta.bot_auth_enabled ? "badge-info" : "badge-warn"}">
              ${meta.hmac_required ? "HMAC: BẮT BUỘC" : meta.bot_auth_enabled ? "HMAC/Bearer: BẬT" : "Auth: TẮT"}
            </span>
          </div>
        </div>

        <div class="grid-2" style="margin-bottom:8px">
          <div class="field" style="margin-bottom:0">
            <label for="gen-token">Secret Token (khớp security.token của server)</label>
            <input type="text" id="gen-token" class="input mono" placeholder="SECRET_TOKEN">
          </div>
          <div class="field" style="margin-bottom:0">
            <label for="gen-interval">Chu kỳ gửi (giây)</label>
            <input type="number" id="gen-interval" class="input mono" value="60" min="10">
          </div>
        </div>

        <div class="field">
          <div class="row-between"><label>File client/config.json (chạy: sudo python3 client/run.py)</label><button type="button" class="btn btn-secondary btn-sm" id="btn-copy-cli">Sao chép</button></div>
          <pre class="code-block" id="out-cli">${esc(buildConfigJson("", 60))}</pre>
        </div>

        <div class="field" style="margin-bottom:0">
          <div class="row-between"><label>File /etc/systemd/system/client-bot.service</label><button type="button" class="btn btn-secondary btn-sm" id="btn-copy-systemd">Sao chép</button></div>
          <pre class="code-block" id="out-systemd">${esc(buildSystemd())}</pre>
        </div>
      </section>

      <!-- Drag & Drop JSON Import + Webhook & Retention -->
      <div class="stack">
        <section class="card">
          <div class="card-head">
            <div>
              <h3 class="card-title">Nạp báo cáo thủ công (JSON Import)</h3>
              <p class="card-sub">Kéo thả file <code>client_bot_report.json</code> vào đây để trực quan hóa ngay</p>
            </div>
          </div>
          <div class="dropzone" id="json-dropzone" tabindex="0" role="button">
            <strong>Kéo thả file .json vào đây</strong>
            <div class="muted-sm" style="margin-top:4px">hoặc nhấp để chọn file từ máy tính</div>
          </div>
        </section>

        <section class="card">
          <div class="card-head">
            <div>
              <h3 class="card-title">Cảnh báo Webhook &amp; Lưu trữ</h3>
              <p class="card-sub">Tự động gửi thông báo tới Discord, Slack hoặc Webhook HTTP khi có cảnh báo mới</p>
            </div>
          </div>
          <form id="settings-form">
            <div class="field">
              <label class="checkbox-pill" style="width:fit-content">
                <input type="checkbox" id="set-wh-enabled" ${settings.webhook_enabled ? "checked" : ""}>
                <span>Bật gửi Webhook tự động</span>
              </label>
            </div>
            <div class="field">
              <label for="set-wh-url">Webhook URL (Discord / Slack / HTTP)</label>
              <input type="url" id="set-wh-url" class="input mono" value="${esc(settings.webhook_url || "")}" placeholder="https://discord.com/api/webhooks/...">
            </div>
            <div class="grid-3" style="margin-bottom:12px">
              <div class="field" style="margin-bottom:0">
                <label for="set-wh-sev">Mức tối thiểu</label>
                <select id="set-wh-sev" class="select">
                  ${["info", "warning", "critical"].map((s) => `<option value="${s}" ${settings.webhook_min_severity === s ? "selected" : ""}>${s.toUpperCase()}</option>`).join("")}
                </select>
              </div>
              <div class="field" style="margin-bottom:0">
                <label for="set-stale">Stale (giây)</label>
                <input type="number" id="set-stale" class="input mono" min="30" value="${Number(settings.stale_seconds || 180)}">
              </div>
              <div class="field" style="margin-bottom:0">
                <label for="set-retain">Giữ lịch sử (ngày)</label>
                <input type="number" id="set-retain" class="input mono" min="1" value="${Number(settings.retain_days || 14)}">
              </div>
            </div>
            <div class="row-wrap">
              <button type="submit" class="btn btn-primary">Lưu cấu hình</button>
              <button type="button" class="btn btn-secondary" id="btn-test-wh">Gửi thử Webhook</button>
            </div>
          </form>
        </section>
      </div>
    </div>

    <!-- Host Management -->
    <section class="card">
      <div class="card-head">
        <div>
          <h3 class="card-title">Quản lý Máy chủ đã đăng ký (${hosts.length})</h3>
          <p class="card-sub">Xóa các máy chủ cũ không còn giám sát khỏi cơ sở dữ liệu SQLite</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr><th>Host</th><th>Trạng thái</th><th>Báo cáo gần nhất</th><th class="num">Số mẫu lưu</th><th>Ghi chú</th><th>Hành động</th></tr>
          </thead>
          <tbody>
            ${hosts.length === 0
              ? `<tr><td colspan="6" class="muted-sm" style="text-align:center;padding:18px">Chưa có máy chủ nào.</td></tr>`
              : hosts.map((h) => `
                <tr>
                  <td class="mono"><strong>${esc(h.host)}</strong></td>
                  <td><span class="badge ${h.status === "online" ? "badge-ok" : h.status === "stale" ? "badge-warn" : "badge-crit"}">${statusLabel(h.status)}</span></td>
                  <td class="mono-muted">${fmtAge(h.age_seconds)}</td>
                  <td class="num">${h.reports_count}</td>
                  <td class="muted-sm">${esc(h.note || "—")}</td>
                  <td>
                    <div class="row-wrap">
                      <a href="#/host/${encodeURIComponent(h.host)}" class="btn btn-secondary btn-sm">Xem</a>
                      <button type="button" class="btn btn-danger btn-sm" data-del-host="${esc(h.host)}">Xóa</button>
                    </div>
                  </td>
                </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;

  // Live config generator
  const updateGen = () => {
    const tok = root.querySelector("#gen-token").value.trim();
    const iv = root.querySelector("#gen-interval").value || 60;
    root.querySelector("#out-cli").textContent = buildConfigJson(tok, iv);
    root.querySelector("#out-systemd").textContent = buildSystemd();
  };
  root.querySelector("#gen-token")?.addEventListener("input", updateGen);
  root.querySelector("#gen-interval")?.addEventListener("input", updateGen);
  root.querySelector("#btn-copy-cli")?.addEventListener("click", () => copyText(root.querySelector("#out-cli").textContent, ctx.toast));
  root.querySelector("#btn-copy-systemd")?.addEventListener("click", () => copyText(root.querySelector("#out-systemd").textContent, ctx.toast));

  // Drag & Drop Import
  const dz = root.querySelector("#json-dropzone");
  dz?.addEventListener("click", () => ctx.triggerImport());
  dz?.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
  dz?.addEventListener("dragleave", () => dz.classList.remove("dragover"));
  dz?.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");
    const file = e.dataTransfer?.files?.[0];
    if (file) ctx.importFile(file);
  });

  // Helper to prompt Bearer token if needed on write
  const runWrite = async (fn) => {
    try {
      return await fn();
    } catch (err) {
      if (err.status === 401 && meta.bot_auth_enabled && !meta.ui_auth_enabled) {
        const tok = window.prompt("Server đang bật --token. Nhập Bearer token để lưu thay đổi:");
        if (!tok) return null;
        setBearerToken(tok);
        return await fn();
      }
      throw err;
    }
  };

  // Save Settings
  root.querySelector("#settings-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      webhook_enabled: root.querySelector("#set-wh-enabled").checked,
      webhook_url: root.querySelector("#set-wh-url").value.trim(),
      webhook_min_severity: root.querySelector("#set-wh-sev").value,
      stale_seconds: Number(root.querySelector("#set-stale").value || 180),
      retain_days: Number(root.querySelector("#set-retain").value || 14),
    };
    try {
      const ok = await runWrite(() => apiPost("/api/settings", payload));
      if (ok) {
        ctx.toast("Đã lưu cấu hình thành công", "ok");
        ctx.refresh();
      }
    } catch (err) {
      ctx.toast(`Lỗi lưu cấu hình: ${err.message}`, "err");
    }
  });

  // Test Webhook
  root.querySelector("#btn-test-wh")?.addEventListener("click", async () => {
    const url = root.querySelector("#set-wh-url").value.trim();
    if (!url) {
      ctx.toast("Vui lòng nhập Webhook URL trước khi test", "err");
      return;
    }
    try {
      const res = await runWrite(() => apiPost("/api/settings/webhook-test", { webhook_url: url }));
      if (res) ctx.toast(`Webhook OK (${res.detail})`, "ok");
    } catch (err) {
      ctx.toast(`Webhook thất bại: ${err.message}`, "err");
    }
  });

  // Delete Host
  root.querySelectorAll("[data-del-host]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const host = btn.dataset.delHost;
      if (!window.confirm(`Xóa toàn bộ dữ liệu và lịch sử của máy chủ "${host}"?`)) return;
      try {
        const res = await runWrite(() => apiDelete(`/api/hosts/${encodeURIComponent(host)}`));
        if (res) {
          ctx.toast(`Đã xóa máy chủ "${host}"`, "ok");
          ctx.refresh();
        }
      } catch (err) {
        ctx.toast(`Lỗi xóa host: ${err.message}`, "err");
      }
    });
  });
}

/** View: Server-side Account & Research Group Registry (Key Members & Student Groups 1-3+ members). */
import { api, apiDelete, apiPost } from "../api.js";
import { openEmailModal } from "../email_modal.js";
import { copyText, esc, fmtDateTime, fmtMB } from "../format.js";

const TYPE_META = {
  key_member: { label: "Key Member", cls: "badge-info" },
  student_group: { label: "Nhóm SV", cls: "badge-ok" },
  admin: { label: "Admin", cls: "badge-warn" },
  other: { label: "Khác", cls: "badge-muted" },
};

const STATUS_META = {
  active: { label: "Hoạt động", cls: "badge-ok" },
  unassigned: { label: "Chưa gán chủ", cls: "badge-warn" },
  suspended: { label: "Tạm khóa", cls: "badge-crit" },
  archived: { label: "Lưu trữ", cls: "badge-muted" },
};

const ROLE_LABELS = {
  key_member: "Key Member",
  leader: "Trưởng nhóm",
  member: "Thành viên",
};

const localState = {
  search: "",
  host: "all",
  type: "all",
  status: "all",
  data: null,
};

function renderExpiryBadge(acc) {
  if (!acc.expires_at) return `<span class="mono-muted">Không thời hạn</span>`;
  const d = acc.days_until_expiry;
  if (acc.expiry_state === "expired") {
    return `<span class="badge badge-crit" title="Hết hạn ${esc(acc.expires_at)}">Quá hạn ${Math.abs(d)}d</span>`;
  }
  if (acc.expiry_state === "expiring") {
    return `<span class="badge badge-warn" title="Hết hạn ${esc(acc.expires_at)}">Còn ${d}d (${esc(acc.expires_at)})</span>`;
  }
  return `<span class="mono-muted">${esc(acc.expires_at)} (${d}d)</span>`;
}

function buildProvisioningCommands(acc) {
  const u = acc.username;
  const groupNote = acc.group_name ? ` -c "${acc.group_name.replace(/"/g, "")}"` : "";
  return [
    {
      title: "Tạo tài khoản Linux / JupyterHub mới trên máy chủ",
      cmd: `sudo useradd -m -s /bin/bash${groupNote} ${u} && sudo passwd ${u}`,
    },
    {
      title: "Khóa đăng nhập & dừng tiến trình đang chạy",
      cmd: `sudo usermod -L -e 1 ${u} && sudo pkill -TERM -u ${u}`,
    },
    {
      title: "Mở khóa tài khoản",
      cmd: `sudo usermod -U -e "" ${u}`,
    },
    {
      title: "Sao lưu thư mục /home trước khi thu hồi",
      cmd: `sudo mkdir -p /var/backups/accounthub && sudo tar -czf /var/backups/accounthub/${u}_$(date +%F).tar.gz /home/${u}`,
    },
  ];
}

export async function renderRegistryView(root, ctx) {
  let payload;
  try {
    payload = await api("/api/registry/accounts");
    localState.data = payload;
  } catch (err) {
    root.innerHTML = `<div class="card"><p class="field-error">Không tải được sổ quản lý tài khoản: ${esc(err.message)}</p></div>`;
    return;
  }

  const allAccounts = payload.accounts || [];
  const summary = payload.summary || {};
  const knownHosts = Array.from(
    new Set([
      ...(ctx.fleet?.hosts || []).map((h) => h.host),
      ...allAccounts.map((a) => a.host),
    ])
  ).sort();

  const q = localState.search.trim().toLowerCase();
  let rows = allAccounts.slice();
  if (localState.host !== "all") rows = rows.filter((a) => a.host === localState.host);
  if (localState.type !== "all") rows = rows.filter((a) => a.account_type === localState.type);
  if (localState.status === "expiring") {
    rows = rows.filter((a) => a.expiry_state === "expiring" || a.expiry_state === "expired");
  } else if (localState.status !== "all") {
    rows = rows.filter((a) => a.status === localState.status);
  }
  if (q) {
    rows = rows.filter((a) => {
      const hay = [
        a.host, a.username, a.group_name, a.advisor, a.purpose, a.notes,
        ...(a.members || []).flatMap((m) => [m.full_name, m.student_id, m.email, m.phone]),
      ].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }

  root.innerHTML = `
    <!-- KPI Strip -->
    <div class="grid-5">
      <article class="kpi-card clickable-card" data-quick-status="all">
        <div class="row-between"><span class="kpi-label">Tổng tài khoản</span><span class="badge badge-info">${knownHosts.length} cụm</span></div>
        <div class="kpi-val">${summary.total || 0}</div>
        <div class="kpi-foot"><span>Tổng thành viên: <strong>${summary.total_people || 0}</strong></span></div>
      </article>

      <article class="kpi-card clickable-card" data-quick-type="key_member">
        <div class="row-between"><span class="kpi-label">Key Members</span><span class="badge badge-info">Chủ chốt</span></div>
        <div class="kpi-val">${summary.key_members || 0}</div>
        <div class="kpi-foot"><span>GV / Nghiên cứu viên</span></div>
      </article>

      <article class="kpi-card clickable-card" data-quick-type="student_group">
        <div class="row-between"><span class="kpi-label">Nhóm Sinh viên</span><span class="badge badge-ok">1–3+ SV</span></div>
        <div class="kpi-val">${summary.student_groups || 0}</div>
        <div class="kpi-foot"><span>Tài khoản theo đề tài</span></div>
      </article>

      <article class="kpi-card clickable-card" data-quick-status="unassigned">
        <div class="row-between"><span class="kpi-label">Chưa gán chủ</span><span class="badge ${summary.unassigned > 0 ? "badge-warn" : "badge-ok"}">${summary.unassigned > 0 ? "Cần bổ sung" : "Đủ"}</span></div>
        <div class="kpi-val">${summary.unassigned || 0}</div>
        <div class="kpi-foot"><span>User cũ đồng bộ từ host</span></div>
      </article>

      <article class="kpi-card clickable-card" data-quick-status="expiring">
        <div class="row-between"><span class="kpi-label">Sắp / Hết hạn</span><span class="badge ${summary.expiring_or_expired > 0 ? "badge-crit" : "badge-ok"}">≤ 14 ngày</span></div>
        <div class="kpi-val">${summary.expiring_or_expired || 0}</div>
        <div class="kpi-foot"><span>Tạm khóa: <strong>${summary.suspended || 0}</strong></span></div>
      </article>
    </div>

    <!-- Toolbar & Directory Table -->
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">Sổ quản lý Tài khoản &amp; Nhóm nghiên cứu</h2>
          <p class="card-sub">Đồng bộ tài khoản cũ từ các cụm máy, gán Key Member hoặc nhóm 1–3 sinh viên và quản lý vòng đời trực tiếp</p>
        </div>
        <div class="row-wrap">
          <button type="button" class="btn btn-primary" id="btn-reg-create">+ Tạo tài khoản mới</button>
          <button type="button" class="btn btn-secondary" id="btn-reg-sync">Đồng bộ user cũ từ máy chủ</button>
          <button type="button" class="btn btn-secondary" id="btn-reg-import">Import CSV</button>
          <a href="/api/registry/template-csv" class="btn btn-secondary btn-sm">CSV mẫu</a>
          <a href="/api/registry/export-csv${localState.host !== "all" ? `?host=${encodeURIComponent(localState.host)}` : ""}" class="btn btn-secondary btn-sm">Xuất CSV</a>
        </div>
      </div>

      <!-- Filters -->
      <div class="row-between" style="margin-bottom:12px">
        <div class="row-wrap">
          <input type="search" id="reg-search" class="input input-search" value="${esc(localState.search)}" placeholder="Tìm username, tên nhóm, sinh viên, MSSV, email, GVHD...">
          <select id="reg-host-filter" class="select mono">
            <option value="all" ${localState.host === "all" ? "selected" : ""}>Tất cả cụm máy (${knownHosts.length})</option>
            ${knownHosts.map((h) => `<option value="${esc(h)}" ${localState.host === h ? "selected" : ""}>${esc(h)}</option>`).join("")}
          </select>
          <select id="reg-type-filter" class="select">
            <option value="all" ${localState.type === "all" ? "selected" : ""}>Tất cả loại</option>
            <option value="key_member" ${localState.type === "key_member" ? "selected" : ""}>Key Member</option>
            <option value="student_group" ${localState.type === "student_group" ? "selected" : ""}>Nhóm sinh viên</option>
            <option value="admin" ${localState.type === "admin" ? "selected" : ""}>Admin</option>
            <option value="other" ${localState.type === "other" ? "selected" : ""}>Khác</option>
          </select>
          <select id="reg-status-filter" class="select">
            <option value="all" ${localState.status === "all" ? "selected" : ""}>Tất cả trạng thái</option>
            <option value="active" ${localState.status === "active" ? "selected" : ""}>Đang hoạt động</option>
            <option value="unassigned" ${localState.status === "unassigned" ? "selected" : ""}>Chưa gán chủ</option>
            <option value="expiring" ${localState.status === "expiring" ? "selected" : ""}>Sắp / Đã hết hạn</option>
            <option value="suspended" ${localState.status === "suspended" ? "selected" : ""}>Tạm khóa</option>
            <option value="archived" ${localState.status === "archived" ? "selected" : ""}>Lưu trữ</option>
          </select>
        </div>
        <span class="mono-muted">Hiển thị ${rows.length} / ${allAccounts.length} tài khoản</span>
      </div>

      ${allAccounts.length === 0 ? `
        <div class="item-box" style="text-align:center;padding:28px">
          <h3 style="margin:0 0 6px">Chưa có tài khoản nào trong sổ quản lý</h3>
          <p class="muted-sm" style="margin:0 0 14px">Bạn có thể bấm <strong>Đồng bộ user cũ từ máy chủ</strong> để nhập toàn bộ user hiện có trên các cụm máy (vd <code>bmtt</code>), hoặc <strong>Import CSV</strong> từ danh sách quản lý cũ.</p>
          <div class="row-wrap" style="justify-content:center">
            <button type="button" class="btn btn-primary" id="btn-empty-sync">Đồng bộ user cũ ngay</button>
            <button type="button" class="btn btn-secondary" id="btn-empty-create">+ Tạo tài khoản mới</button>
          </div>
        </div>
      ` : `
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>Tài khoản / Cụm</th>
                <th>Loại &amp; Nhóm / GVHD</th>
                <th>Thành viên (1–3+ người)</th>
                <th>Tài nguyên thực tế</th>
                <th>Trạng thái &amp; Hạn</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody>
              ${rows.length === 0
      ? `<tr><td colspan="6" class="muted-sm" style="text-align:center;padding:20px">Không có tài khoản nào khớp bộ lọc.</td></tr>`
      : rows.map((a) => {
        const tMeta = TYPE_META[a.account_type] || TYPE_META.other;
        const sMeta = STATUS_META[a.status] || STATUS_META.active;
        const t = a.telemetry || {};
        const members = a.members || [];
        return `
                      <tr>
                        <td>
                          <div class="row-wrap" style="gap:6px">
                            <span class="status-dot ${t.online ? "status-online" : t.exists_on_host ? "status-unknown" : "status-stale"}"
                              title="${t.online ? "Đang online" : t.exists_on_host ? "Đang tồn tại trên máy chủ" : "Chưa thấy trên báo cáo máy chủ"}"></span>
                            <strong class="mono">${esc(a.username)}</strong>
                          </div>
                          <div class="row-wrap" style="margin-top:4px;gap:5px">
                            <span class="badge badge-muted">${esc(a.host)}</span>
                            ${!t.exists_on_host ? `<span class="badge badge-warn" title="Tài khoản chưa tạo hoặc chưa xuất hiện trên máy chủ">Chưa có trên host</span>` : ""}
                            ${t.gpu_idle_mb > 0 ? `<span class="badge badge-warn">GPU-IDLE ${t.gpu_idle_mb}M</span>` : ""}
                          </div>
                        </td>
                        <td>
                          <div class="row-wrap" style="gap:6px;margin-bottom:3px">
                            <span class="badge ${tMeta.cls}">${tMeta.label}${a.account_type === "student_group" ? ` (${members.length})` : ""}</span>
                            <strong>${esc(a.group_name || (a.status === "unassigned" ? "Chưa gán nhóm / đề tài" : "—"))}</strong>
                          </div>
                          ${a.advisor ? `<div class="muted-sm">GVHD: <strong>${esc(a.advisor)}</strong></div>` : ""}
                          ${a.purpose ? `<div class="muted-sm">${esc(a.purpose)}</div>` : ""}
                        </td>
                        <td>
                          ${members.length === 0
            ? `<span class="muted-sm">Chưa có thành viên</span>`
            : `<div class="member-chips">${members.map((m) => `
                                <div class="member-chip">
                                  <strong>${esc(m.full_name)}</strong>
                                  ${m.student_id ? `<span class="mono-muted">(${esc(m.student_id)})</span>` : ""}
                                  ${m.role === "leader" ? `<span class="badge badge-info" style="padding:0 5px;font-size:10px">Leader</span>` : ""}
                                  ${m.email || m.phone ? `<div class="mono-muted" style="font-size:11px">${esc([m.email, m.phone].filter(Boolean).join(" · "))}</div>` : ""}
                                </div>`).join("")}</div>`}
                        </td>
                        <td>
                          ${t.exists_on_host ? `
                            <div class="mono-muted">Disk: <strong style="color:var(--fg)">${t.disk_mb != null ? fmtMB(t.disk_mb) : "N/A"}</strong>${a.quota_disk_gb ? ` / ${a.quota_disk_gb}G` : ""}</div>
                            <div class="mono-muted">RAM: <strong style="color:var(--fg)">${fmtMB(t.mem_mb || 0)}</strong> · VRAM: <strong style="color:var(--violet)">${t.gpu_mem_mb ? fmtMB(t.gpu_mem_mb) : "0"}</strong></div>
                            <div class="mono-muted">Idle: ${t.days_inactive != null ? `${t.days_inactive} ngày` : "Chưa SSH"}</div>
                          ` : `<span class="muted-sm">Chờ đồng bộ từ host</span>`}
                        </td>
                        <td>
                          <div style="margin-bottom:4px"><span class="badge ${sMeta.cls}">${sMeta.label}</span></div>
                          <div>${renderExpiryBadge(a)}</div>
                        </td>
                        <td>
                          <div class="row-wrap" style="gap:5px">
                            <button type="button" class="btn btn-secondary btn-sm" data-edit-acc="${a.id}">${a.status === "unassigned" ? "Gán chủ" : "Sửa"}</button>
                            <button type="button" class="btn btn-secondary btn-sm" data-mail-acc="${a.id}">Gửi mail</button>
                            <button type="button" class="btn btn-secondary btn-sm" data-manage-acc="${a.id}">Quản trị</button>
                            <button type="button" class="btn btn-danger btn-sm" data-del-acc="${a.id}" data-del-name="${esc(a.username)}">Xóa</button>
                          </div>
                        </td>
                      </tr>`;
      }).join("")}
            </tbody>
          </table>
        </div>
      `}
    </section>

    <!-- Modal Host Container -->
    <div id="reg-modal-host"></div>
  `;

  // Quick KPI Filters
  root.querySelectorAll("[data-quick-status]").forEach((el) => {
    el.addEventListener("click", () => {
      localState.status = el.dataset.quickStatus;
      localState.type = "all";
      renderRegistryView(root, ctx);
    });
  });
  root.querySelectorAll("[data-quick-type]").forEach((el) => {
    el.addEventListener("click", () => {
      localState.type = el.dataset.quickType;
      localState.status = "all";
      renderRegistryView(root, ctx);
    });
  });

  // Search & Select Filters
  root.querySelector("#reg-search")?.addEventListener("input", (e) => {
    localState.search = e.target.value;
    renderRegistryView(root, ctx);
    const inp = root.querySelector("#reg-search");
    inp?.focus();
    inp?.setSelectionRange(inp.value.length, inp.value.length);
  });
  root.querySelector("#reg-host-filter")?.addEventListener("change", (e) => { localState.host = e.target.value; renderRegistryView(root, ctx); });
  root.querySelector("#reg-type-filter")?.addEventListener("change", (e) => { localState.type = e.target.value; renderRegistryView(root, ctx); });
  root.querySelector("#reg-status-filter")?.addEventListener("change", (e) => { localState.status = e.target.value; renderRegistryView(root, ctx); });

  // Sync from Host(s)
  const doSync = async () => {
    const targetHost = localState.host !== "all" ? localState.host : "";
    try {
      const res = await apiPost("/api/registry/sync", { host: targetHost });
      ctx.toast(`Đồng bộ xong: thêm ${res.added} user mới, bỏ qua ${res.skipped} user đã có`, "ok");
      renderRegistryView(root, ctx);
      ctx.refreshRegistryBadge?.();
    } catch (err) {
      ctx.toast(`Lỗi đồng bộ: ${err.message}`, "err");
    }
  };
  root.querySelector("#btn-reg-sync")?.addEventListener("click", doSync);
  root.querySelector("#btn-empty-sync")?.addEventListener("click", doSync);

  const modalTarget = () => ctx.modalRoot || root.querySelector("#reg-modal-host");

  // Create / Edit Modal
  const openEditor = (acc = null) => openAccountModal(modalTarget(), acc, knownHosts, ctx, () => {
    renderRegistryView(root, ctx);
    ctx.refreshRegistryBadge?.();
  });

  root.querySelector("#btn-reg-create")?.addEventListener("click", () => openEditor(null));
  root.querySelector("#btn-empty-create")?.addEventListener("click", () => openEditor(null));

  root.querySelectorAll("[data-edit-acc]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const acc = allAccounts.find((x) => String(x.id) === btn.dataset.editAcc);
      if (acc) openEditor(acc);
    });
  });

  root.querySelectorAll("[data-mail-acc]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const acc = allAccounts.find((x) => String(x.id) === btn.dataset.mailAcc);
      if (!acc) return;
      const defaultKind = acc.expiry_state === "expiring" || acc.expiry_state === "expired" ? "expiry" : "general";
      openEmailModal(modalTarget(), { account_id: acc.id, kind: defaultKind, owner: acc }, ctx);
    });
  });

  // Manage Lifecycle & Commands Modal
  root.querySelectorAll("[data-manage-acc]").forEach((btn) => {
    btn.addEventListener("click", () => {
      openManageModal(modalTarget(), Number(btn.dataset.manageAcc), ctx, () => {
        renderRegistryView(root, ctx);
        ctx.refreshRegistryBadge?.();
      });
    });
  });

  // Delete Account from Registry
  root.querySelectorAll("[data-del-acc]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!window.confirm(`Xóa bản ghi quản lý cho "${btn.dataset.delName}" khỏi hệ thống? (Không xóa user trên Linux host)`)) return;
      try {
        await apiDelete(`/api/registry/accounts/${btn.dataset.delAcc}`);
        ctx.toast(`Đã xóa "${btn.dataset.delName}" khỏi sổ quản lý`, "ok");
        renderRegistryView(root, ctx);
        ctx.refreshRegistryBadge?.();
      } catch (err) {
        ctx.toast(`Lỗi xóa: ${err.message}`, "err");
      }
    });
  });

  // Import CSV Modal
  root.querySelector("#btn-reg-import")?.addEventListener("click", () => {
    openCsvImportModal(modalTarget(), ctx, () => {
      renderRegistryView(root, ctx);
      ctx.refreshRegistryBadge?.();
    });
  });
}

// ------------------------------------------------------------ Account Editor Modal
function openAccountModal(hostEl, acc, knownHosts, ctx, onSaved) {
  const isEdit = Boolean(acc);
  const initialMembers = (acc?.members?.length ? acc.members : [{ full_name: "", student_id: "", email: "", phone: "", role: acc?.account_type === "key_member" ? "key_member" : "leader" }]);
  const defaultHost = acc?.host || ctx.selectedHost || knownHosts[0] || "";

  hostEl.innerHTML = `
    <div class="modal-backdrop" id="acc-modal-bg" role="dialog" aria-modal="true">
      <form class="modal-card modal-lg" id="acc-modal-form">
        <div class="card-head">
          <div>
            <h3 class="card-title">${isEdit ? `Chỉnh sửa / Gán chủ sở hữu: ${esc(acc.username)}` : "Tạo tài khoản quản lý mới"}</h3>
            <p class="card-sub">Gán cho Key Member hoặc nhóm 1–3+ sinh viên nghiên cứu</p>
          </div>
          <button type="button" class="icon-btn" id="acc-modal-close" aria-label="Đóng">✕</button>
        </div>

        <div class="grid-3" style="margin-bottom:10px">
          <div class="field" style="margin-bottom:0">
            <label for="f-host">Cụm máy / Host *</label>
            <input id="f-host" class="input mono" list="known-hosts-list" value="${esc(defaultHost)}" required placeholder="vd: bmtt">
            <datalist id="known-hosts-list">${knownHosts.map((h) => `<option value="${esc(h)}">`).join("")}</datalist>
          </div>
          <div class="field" style="margin-bottom:0">
            <label for="f-user">Username hệ thống *</label>
            <input id="f-user" class="input mono" value="${esc(acc?.username || "")}" required placeholder="vd: jupyter-iec2026ai01">
          </div>
          <div class="field" style="margin-bottom:0">
            <label for="f-type">Loại tài khoản</label>
            <select id="f-type" class="select">
              <option value="student_group" ${(acc?.account_type || "student_group") === "student_group" ? "selected" : ""}>Nhóm sinh viên (1–3+ SV)</option>
              <option value="key_member" ${acc?.account_type === "key_member" ? "selected" : ""}>Key Member (GV / NCV)</option>
              <option value="admin" ${acc?.account_type === "admin" ? "selected" : ""}>Quản trị (Admin)</option>
              <option value="other" ${acc?.account_type === "other" ? "selected" : ""}>Khác</option>
            </select>
          </div>
        </div>

        <div class="grid-2" style="margin-bottom:10px">
          <div class="field" style="margin-bottom:0">
            <label for="f-group">Tên nhóm nghiên cứu / Đề tài</label>
            <input id="f-group" class="input" value="${esc(acc?.group_name || "")}" placeholder="vd: Nhóm AI Y tế K2024 / Paper ICLR 2026">
          </div>
          <div class="field" style="margin-bottom:0">
            <label for="f-advisor">Giảng viên hướng dẫn / Phụ trách</label>
            <input id="f-advisor" class="input" value="${esc(acc?.advisor || "")}" placeholder="vd: TS. Lê Khắc Hưng">
          </div>
        </div>

        <div class="grid-3" style="margin-bottom:10px">
          <div class="field" style="margin-bottom:0">
            <label for="f-status">Trạng thái</label>
            <select id="f-status" class="select">
              <option value="active" ${(acc?.status || "active") === "active" ? "selected" : ""}>Đang hoạt động</option>
              <option value="unassigned" ${acc?.status === "unassigned" ? "selected" : ""}>Chưa gán chủ</option>
              <option value="suspended" ${acc?.status === "suspended" ? "selected" : ""}>Tạm khóa</option>
              <option value="archived" ${acc?.status === "archived" ? "selected" : ""}>Lưu trữ</option>
            </select>
          </div>
          <div class="field" style="margin-bottom:0">
            <label for="f-exp">Ngày hết hạn (tùy chọn)</label>
            <input id="f-exp" type="date" class="input mono" value="${esc(acc?.expires_at || "")}">
          </div>
          <div class="field" style="margin-bottom:0">
            <label for="f-quota">Hạn mức Disk (GB)</label>
            <input id="f-quota" type="number" step="1" min="0" class="input mono" value="${acc?.quota_disk_gb ?? ""}" placeholder="vd: 100">
          </div>
        </div>

        <div class="grid-2" style="margin-bottom:12px">
          <div class="field" style="margin-bottom:0">
            <label for="f-purpose">Mục đích sử dụng</label>
            <input id="f-purpose" class="input" value="${esc(acc?.purpose || "")}" placeholder="vd: Khóa luận tốt nghiệp / Huấn luyện mô hình...">
          </div>
          <div class="field" style="margin-bottom:0">
            <label for="f-notes">Ghi chú thêm / Cấp phát GPU</label>
            <input id="f-notes" class="input" value="${esc(acc?.notes || "")}" placeholder="vd: Dùng GPU 0, liên hệ Zalo khi cần reset">
          </div>
        </div>

        <!-- Dynamic Members List (1-3+ students or Key Member) -->
        <div class="item-box" style="margin-bottom:14px">
          <div class="row-between" style="margin-bottom:8px">
            <span class="kpi-label">Danh sách thành viên sử dụng tài khoản</span>
            <button type="button" class="btn btn-secondary btn-sm" id="btn-add-member">+ Thêm thành viên</button>
          </div>
          <div class="stack" id="members-editor-list"></div>
        </div>

        <div class="row-between">
          <span class="muted-sm">Các thành viên giúp liên hệ ngay khi có cảnh báo GPU-IDLE hoặc ổ đĩa đầy.</span>
          <div class="row-wrap">
            <button type="button" class="btn btn-secondary" id="acc-modal-cancel">Hủy</button>
            <button type="submit" class="btn btn-primary">Lưu thông tin</button>
          </div>
        </div>
      </form>
    </div>
  `;

  const close = () => { hostEl.innerHTML = ""; };
  hostEl.querySelector("#acc-modal-close")?.addEventListener("click", close);
  hostEl.querySelector("#acc-modal-cancel")?.addEventListener("click", close);
  hostEl.querySelector("#acc-modal-bg")?.addEventListener("click", (e) => { if (e.target.id === "acc-modal-bg") close(); });

  const listEl = hostEl.querySelector("#members-editor-list");
  const addMemberRow = (m = {}) => {
    const row = document.createElement("div");
    row.className = "member-edit-row";
    row.innerHTML = `
      <input class="input m-name" placeholder="Họ và tên *" value="${esc(m.full_name || "")}">
      <input class="input mono m-sid" placeholder="MSSV / Mã CB" value="${esc(m.student_id || "")}">
      <input class="input m-email" placeholder="Email" value="${esc(m.email || "")}">
      <input class="input mono m-phone" placeholder="SĐT / Zalo" value="${esc(m.phone || "")}">
      <select class="select m-role">
        ${Object.entries(ROLE_LABELS).map(([k, lbl]) => `<option value="${k}" ${(m.role || "member") === k ? "selected" : ""}>${lbl}</option>`).join("")}
      </select>
      <button type="button" class="icon-btn m-rm" title="Xóa dòng">✕</button>
    `;
    row.querySelector(".m-rm").addEventListener("click", () => row.remove());
    listEl.appendChild(row);
  };

  initialMembers.forEach(addMemberRow);
  hostEl.querySelector("#btn-add-member").addEventListener("click", () => addMemberRow({ role: "member" }));

  hostEl.querySelector("#acc-modal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const members = Array.from(listEl.querySelectorAll(".member-edit-row"))
      .map((r) => ({
        full_name: r.querySelector(".m-name").value.trim(),
        student_id: r.querySelector(".m-sid").value.trim(),
        email: r.querySelector(".m-email").value.trim(),
        phone: r.querySelector(".m-phone").value.trim(),
        role: r.querySelector(".m-role").value,
      }))
      .filter((m) => m.full_name);

    const payload = {
      id: acc?.id,
      host: hostEl.querySelector("#f-host").value.trim(),
      username: hostEl.querySelector("#f-user").value.trim(),
      account_type: hostEl.querySelector("#f-type").value,
      group_name: hostEl.querySelector("#f-group").value.trim(),
      advisor: hostEl.querySelector("#f-advisor").value.trim(),
      status: hostEl.querySelector("#f-status").value,
      expires_at: hostEl.querySelector("#f-exp").value,
      quota_disk_gb: hostEl.querySelector("#f-quota").value,
      purpose: hostEl.querySelector("#f-purpose").value.trim(),
      notes: hostEl.querySelector("#f-notes").value.trim(),
      members,
    };

    try {
      await apiPost("/api/registry/accounts", payload);
      ctx.toast(isEdit ? `Đã cập nhật "${payload.username}"` : `Đã tạo tài khoản "${payload.username}"`, "ok");
      close();
      onSaved();
    } catch (err) {
      ctx.toast(`Lỗi lưu tài khoản: ${err.message}`, "err");
    }
  });
}

// ------------------------------------------------------------ Lifecycle & Provisioning Modal
async function openManageModal(hostEl, accountId, ctx, onChanged) {
  let acc;
  try {
    acc = await api(`/api/registry/accounts/${accountId}`);
  } catch (err) {
    ctx.toast(`Lỗi tải chi tiết: ${err.message}`, "err");
    return;
  }

  const cmds = buildProvisioningCommands(acc);
  const actions = acc.actions || [];
  const sMeta = STATUS_META[acc.status] || STATUS_META.active;

  hostEl.innerHTML = `
    <div class="modal-backdrop" id="mng-modal-bg" role="dialog" aria-modal="true">
      <div class="modal-card modal-lg">
        <div class="card-head">
          <div>
            <h3 class="card-title">Quản trị vòng đời: <span class="mono">${esc(acc.username)}</span> @ <span class="mono">${esc(acc.host)}</span></h3>
            <p class="card-sub">Trạng thái: <span class="badge ${sMeta.cls}">${sMeta.label}</span> · Hạn dùng: ${renderExpiryBadge(acc)}</p>
          </div>
          <button type="button" class="icon-btn" id="mng-close" aria-label="Đóng">✕</button>
        </div>

        <!-- Direct Lifecycle Actions -->
        <div class="item-box" style="margin-bottom:14px">
          <div class="kpi-label" style="margin-bottom:8px">Thao tác quản trị trực tiếp (lưu Audit Log)</div>
          <div class="row-wrap">
            <button type="button" class="btn btn-primary btn-sm" data-act="renew" data-days="30">Gia hạn +30 ngày</button>
            <button type="button" class="btn btn-primary btn-sm" data-act="renew" data-days="90">Gia hạn +90 ngày</button>
            <button type="button" class="btn btn-secondary btn-sm" data-act="renew" data-days="180">Gia hạn +180 ngày</button>
            <button type="button" class="btn btn-secondary btn-sm" data-act="activate">Kích hoạt / Mở khóa</button>
            <button type="button" class="btn btn-danger btn-sm" data-act="suspend">Tạm khóa</button>
            <button type="button" class="btn btn-secondary btn-sm" data-act="archive">Thu hồi &amp; Lưu trữ</button>
          </div>
        </div>

        <!-- Ready-to-run Provisioning Commands -->
        <div class="item-box" style="margin-bottom:14px">
          <div class="kpi-label" style="margin-bottom:8px">Lệnh thực thi trên cụm máy ${esc(acc.host)}</div>
          <div class="stack">
            ${cmds.map((c, idx) => `
              <div>
                <div class="row-between" style="margin-bottom:4px">
                  <span class="muted-sm">${esc(c.title)}</span>
                  <button type="button" class="btn btn-secondary btn-sm" data-copy-prov="${idx}">Copy lệnh</button>
                </div>
                <pre class="code-block" style="padding:8px 10px">${esc(c.cmd)}</pre>
              </div>`).join("")}
          </div>
        </div>

        <!-- Audit History -->
        <div class="item-box">
          <div class="kpi-label" style="margin-bottom:8px">Nhật ký quản trị (${actions.length})</div>
          ${actions.length === 0
      ? `<p class="muted-sm" style="margin:0">Chưa có lịch sử thao tác.</p>`
      : `<div class="stack" style="max-height:180px;overflow:auto">${actions.map((ev) => `
                <div class="row-between" style="border-bottom:1px solid var(--border);padding-bottom:6px">
                  <div><span class="badge badge-muted">${esc(ev.action_type)}</span> <span>${esc(ev.summary)}</span></div>
                  <span class="mono-muted">${fmtDateTime(ev.created_at)}</span>
                </div>`).join("")}</div>`}
        </div>
      </div>
    </div>
  `;

  const close = () => { hostEl.innerHTML = ""; };
  hostEl.querySelector("#mng-close")?.addEventListener("click", close);
  hostEl.querySelector("#mng-modal-bg")?.addEventListener("click", (e) => { if (e.target.id === "mng-modal-bg") close(); });

  hostEl.querySelectorAll("[data-copy-prov]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cmd = cmds[Number(btn.dataset.copyProv)].cmd;
      copyText(cmd, ctx.toast, "Đã sao chép lệnh thực thi");
    });
  });

  hostEl.querySelectorAll("[data-act]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action_type = btn.dataset.act;
      const renew_days = btn.dataset.days ? Number(btn.dataset.days) : undefined;
      try {
        await apiPost(`/api/registry/accounts/${accountId}/actions`, { action_type, renew_days });
        ctx.toast("Đã cập nhật vòng đời tài khoản", "ok");
        onChanged();
        openManageModal(hostEl, accountId, ctx, onChanged);
      } catch (err) {
        ctx.toast(`Lỗi thao tác: ${err.message}`, "err");
      }
    });
  });
}

// ------------------------------------------------------------ CSV Bulk Import Modal
function openCsvImportModal(hostEl, ctx, onImported) {
  hostEl.innerHTML = `
    <div class="modal-backdrop" id="csv-modal-bg" role="dialog" aria-modal="true">
      <form class="modal-card modal-lg" id="csv-modal-form">
        <div class="card-head">
          <div>
            <h3 class="card-title">Import danh sách tài khoản cũ từ CSV / Excel</h3>
            <p class="card-sub">Hỗ trợ nhiều sinh viên trong 1 cột <code>members</code> cách nhau bởi dấu <code>;</code> (<code>Họ tên|MSSV|Email|SĐT|leader; ...</code>) hoặc nhiều dòng cùng <code>host,username</code></p>
          </div>
          <button type="button" class="icon-btn" id="csv-close" aria-label="Đóng">✕</button>
        </div>

        <div class="row-wrap" style="margin-bottom:10px">
          <input type="file" id="csv-file-input" accept=".csv,text/csv" class="input">
          <a href="/api/registry/template-csv" class="btn btn-secondary btn-sm">Tải file CSV mẫu</a>
        </div>

        <div class="field">
          <label for="csv-textarea">Hoặc dán trực tiếp nội dung CSV vào đây:</label>
          <textarea id="csv-textarea" class="input mono" rows="10" placeholder="host,username,account_type,group_name,advisor,status,expires_at,quota_disk_gb,purpose,members,notes"></textarea>
        </div>

        <div class="row-between">
          <span class="muted-sm">Nếu trùng (host, username), hệ thống sẽ cập nhật thông tin mới nhất.</span>
          <div class="row-wrap">
            <button type="button" class="btn btn-secondary" id="csv-cancel">Hủy</button>
            <button type="submit" class="btn btn-primary">Tiến hành Import</button>
          </div>
        </div>
      </form>
    </div>
  `;

  const close = () => { hostEl.innerHTML = ""; };
  hostEl.querySelector("#csv-close")?.addEventListener("click", close);
  hostEl.querySelector("#csv-cancel")?.addEventListener("click", close);
  hostEl.querySelector("#csv-modal-bg")?.addEventListener("click", (e) => { if (e.target.id === "csv-modal-bg") close(); });

  hostEl.querySelector("#csv-file-input")?.addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    hostEl.querySelector("#csv-textarea").value = await file.text();
  });

  hostEl.querySelector("#csv-modal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const csv = hostEl.querySelector("#csv-textarea").value;
    try {
      const res = await apiPost("/api/registry/import-csv", { csv });
      const errNote = res.errors?.length ? ` (${res.errors.length} dòng lỗi)` : "";
      ctx.toast(`Đã import thành công ${res.imported} tài khoản${errNote}`, res.errors?.length ? "err" : "ok");
      close();
      onImported();
    } catch (err) {
      ctx.toast(`Import CSV thất bại: ${err.message}`, "err");
    }
  });
}

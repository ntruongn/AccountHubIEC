/** Pure Canvas/SVG visualization primitives (HiDPI, offline-ready, theme-aware). */
import { esc, fmtDateTime, fmtTime } from "./format.js";

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** SVG Radial Arc Gauge (0..100%) */
export function renderGaugeSVG(pct, colorVar = "--cyan", size = 62) {
  const val = Math.max(0, Math.min(100, Number(pct || 0)));
  const r = 24;
  const c = 2 * Math.PI * r;
  const dash = (val / 100) * c;
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 60 60" aria-hidden="true" style="flex-shrink:0">
      <circle cx="30" cy="30" r="${r}" fill="none" stroke="var(--border)" stroke-width="5.5"/>
      <circle cx="30" cy="30" r="${r}" fill="none" stroke="var(${colorVar})" stroke-width="5.5"
        stroke-linecap="round" stroke-dasharray="${dash.toFixed(1)} ${c.toFixed(1)}"
        transform="rotate(-90 30 30)"/>
      <text x="30" y="34" text-anchor="middle" fill="var(--fg)"
        font-family="Fira Code, monospace" font-size="11.5" font-weight="700">${Math.round(val)}%</text>
    </svg>`;
}

/** Interactive HiDPI Multi-Series Line/Area Chart */
export function drawLineChart(canvas, tooltipEl, points, series, opts = {}) {
  if (!canvas || !canvas.parentElement) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  const width = Math.max(240, Math.floor(rect.width));
  const height = Math.max(140, Math.floor(rect.height));
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const pad = { top: 14, right: 16, bottom: 26, left: opts.leftPad || 44 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  let yMax = opts.yMax ?? 100;
  if (opts.autoMax && points?.length) {
    let maxFound = 0;
    points.forEach((p) => series.forEach((s) => {
      const v = Number(p[s.key] || 0);
      if (v > maxFound) maxFound = v;
    }));
    yMax = maxFound <= 0 ? 10 : Math.ceil(maxFound * 1.15);
  }

  const gridColor = cssVar("--chart-grid", "rgba(148,163,184,0.14)");
  const labelColor = cssVar("--chart-label", "#7c8ea6");
  const bgStroke = cssVar("--bg", "#0b1220");

  function paint(hoverIdx = -1) {
    ctx.clearRect(0, 0, width, height);

    ctx.font = '11px "Fira Code", monospace';
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    const steps = 4;
    for (let i = 0; i <= steps; i++) {
      const v = (yMax / steps) * i;
      const y = pad.top + plotH - (v / yMax) * plotH;
      ctx.strokeStyle = gridColor;
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + plotW, y);
      ctx.stroke();

      ctx.fillStyle = labelColor;
      const lbl = opts.yFormatter ? opts.yFormatter(v) : (yMax <= 10 ? v.toFixed(1) : Math.round(v));
      ctx.fillText(String(lbl), pad.left - 8, y);
    }

    if (!points || points.length === 0) {
      ctx.fillStyle = labelColor;
      ctx.textAlign = "center";
      ctx.fillText("Chưa có dữ liệu lịch sử", pad.left + plotW / 2, pad.top + plotH / 2);
      return;
    }

    const xFor = (i) => points.length === 1 ? pad.left + plotW / 2 : pad.left + (i / (points.length - 1)) * plotW;
    const yFor = (val) => pad.top + plotH - (Math.max(0, Math.min(yMax, Number(val || 0))) / yMax) * plotH;

    // X labels
    ctx.fillStyle = labelColor;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const idxs = points.length === 1 ? [0] : [0, Math.floor((points.length - 1) / 2), points.length - 1];
    new Set(idxs).forEach((idx) => {
      const ts = points[idx].received_at || points[idx].timestamp;
      ctx.fillText(fmtTime(ts), xFor(idx), pad.top + plotH + 7);
    });

    series.forEach((s) => {
      const coords = [];
      points.forEach((p, i) => {
        if (p[s.key] != null) coords.push({ x: xFor(i), y: yFor(p[s.key]), i });
      });
      if (!coords.length) return;

      if (s.fill && coords.length > 1) {
        ctx.save();
        ctx.fillStyle = s.fill;
        ctx.beginPath();
        ctx.moveTo(coords[0].x, pad.top + plotH);
        coords.forEach((c) => ctx.lineTo(c.x, c.y));
        ctx.lineTo(coords[coords.length - 1].x, pad.top + plotH);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
      }

      if (coords.length > 1) {
        ctx.save();
        ctx.strokeStyle = s.color;
        ctx.lineWidth = 2;
        ctx.setLineDash(s.dash || []);
        ctx.beginPath();
        coords.forEach((c, idx) => (idx === 0 ? ctx.moveTo(c.x, c.y) : ctx.lineTo(c.x, c.y)));
        ctx.stroke();
        ctx.restore();
      }

      coords.forEach((c) => {
        if (coords.length <= 12 || c.i === hoverIdx) {
          ctx.beginPath();
          ctx.arc(c.x, c.y, c.i === hoverIdx ? 4.5 : 3, 0, Math.PI * 2);
          ctx.fillStyle = s.color;
          ctx.fill();
          ctx.strokeStyle = bgStroke;
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
      });
    });

    if (hoverIdx >= 0 && hoverIdx < points.length) {
      const hx = xFor(hoverIdx);
      ctx.save();
      ctx.strokeStyle = labelColor;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(hx, pad.top);
      ctx.lineTo(hx, pad.top + plotH);
      ctx.stroke();
      ctx.restore();
    }
  }

  paint(-1);

  if (!tooltipEl) return;
  canvas.onmousemove = (ev) => {
    if (!points || !points.length) return;
    const box = canvas.getBoundingClientRect();
    const mx = ev.clientX - box.left;
    const ratio = Math.max(0, Math.min(1, (mx - pad.left) / plotW));
    const idx = points.length === 1 ? 0 : Math.round(ratio * (points.length - 1));
    paint(idx);

    const p = points[idx];
    const ts = fmtDateTime(p.received_at || p.timestamp);
    const lines = series
      .filter((s) => p[s.key] != null)
      .map((s) => {
        const valText = s.formatter ? s.formatter(p[s.key]) : `${Number(p[s.key]).toFixed(1)}${s.unit || ""}`;
        return `<div><span style="color:${s.color}">●</span> ${esc(s.label)}: <strong>${esc(valText)}</strong></div>`;
      })
      .join("");

    tooltipEl.innerHTML = `<div style="color:var(--fg-muted);margin-bottom:4px">${esc(ts)}</div>${lines}`;
    tooltipEl.classList.remove("hidden");
    const tx = Math.min(box.width - tooltipEl.offsetWidth - 8, Math.max(8, mx + 12));
    tooltipEl.style.left = `${tx}px`;
    tooltipEl.style.top = "10px";
  };
  canvas.onmouseleave = () => {
    paint(-1);
    tooltipEl.classList.add("hidden");
  };
}

/** Mini Sparkline for Fleet Host Cards */
export function drawSparkline(canvas, values, color = "#38bdf8") {
  if (!canvas || !canvas.parentElement) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  const w = Math.max(120, Math.floor(rect.width));
  const h = Math.max(36, Math.floor(rect.height));
  const dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  if (!values || values.length === 0) return;
  const pts = values.map((v, i) => ({
    x: values.length === 1 ? w / 2 : 4 + (i / (values.length - 1)) * (w - 8),
    y: h - 5 - (Math.max(0, Math.min(100, Number(v || 0))) / 100) * (h - 10),
  }));

  if (pts.length > 1) {
    ctx.fillStyle = "rgba(56, 189, 248, 0.12)";
    ctx.beginPath();
    ctx.moveTo(pts[0].x, h - 4);
    pts.forEach((p) => ctx.lineTo(p.x, p.y));
    ctx.lineTo(pts[pts.length - 1].x, h - 4);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = color;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    pts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
    ctx.stroke();
  }
  const last = pts[pts.length - 1];
  ctx.beginPath();
  ctx.arc(last.x, last.y, 3, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

/** Crisp SVG Donut Chart */
export function renderDonutSVG(segments, totalLabel = "Users", size = 148) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  const r = 52;
  const c = 2 * Math.PI * r;
  let offset = 0;
  const arcs = total === 0
    ? `<circle cx="74" cy="74" r="${r}" fill="none" stroke="var(--border)" stroke-width="16"/>`
    : segments.filter((s) => s.value > 0).map((s) => {
        const len = (s.value / total) * c;
        const el = `<circle cx="74" cy="74" r="${r}" fill="none" stroke="${s.color}" stroke-width="16"
          stroke-dasharray="${len.toFixed(2)} ${c.toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"
          transform="rotate(-90 74 74)"/>`;
        offset += len;
        return el;
      }).join("");

  return `
    <svg width="${size}" height="${size}" viewBox="0 0 148 148" role="img" aria-label="Phân bố ${esc(totalLabel)}">
      ${arcs}
      <text x="74" y="71" text-anchor="middle" fill="var(--fg)" font-family="Fira Code, monospace" font-size="22" font-weight="700">${total}</text>
      <text x="74" y="88" text-anchor="middle" fill="var(--fg-muted)" font-family="Fira Sans, sans-serif" font-size="11">${esc(totalLabel)}</text>
    </svg>`;
}

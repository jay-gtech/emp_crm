/**
 * app/static/js/analytics.js
 * ===========================
 * AI Monitoring dashboard — fetches /analytics/ai/data and renders all
 * charts + tables. Auto-refreshes every 30 seconds.
 */

"use strict";

// ── Chart instances (kept so we can destroy+redraw on refresh) ────────────────
let _chartOutcomes   = null;
let _chartWorkload   = null;
let _chartReasonTags = null;
let _chartMlProb     = null;

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  blue:   "#2563eb",
  green:  "#16a34a",
  rose:   "#e11d48",
  amber:  "#d97706",
  indigo: "#4f46e5",
  teal:   "#0d9488",
  purple: "#7c3aed",
  gray:   "#94a3b8",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(val) {
  if (val === null || val === undefined) return "—";
  return (val * 100).toFixed(1) + "%";
}

function num(val, decimals = 1) {
  if (val === null || val === undefined) return "—";
  return Number(val).toFixed(decimals);
}

function destroyChart(instance) {
  if (instance) { try { instance.destroy(); } catch (_) {} }
}

// ── KPI cards ─────────────────────────────────────────────────────────────────

function renderKpis(sm, modelVersion) {
  const grid = document.getElementById("ai-kpi-grid");
  if (!grid) return;

  const cards = [
    {
      color: "blue",
      icon:  "🤖",
      value: sm.total_assignments ?? "—",
      label: "Total Assignments",
      sub:   "deduplicated",
    },
    {
      color: sm.success_rate !== null && sm.success_rate < 0.6 ? "rose" : "green",
      icon:  "✅",
      value: pct(sm.success_rate),
      label: "Success Rate",
      sub:   "tasks with outcomes",
    },
    {
      color: sm.delay_rate !== null && sm.delay_rate > 0.3 ? "amber" : "teal",
      icon:  "⏱",
      value: pct(sm.delay_rate),
      label: "Delay Rate",
      sub:   "among completed tasks",
    },
    {
      color: "indigo",
      icon:  "🧠",
      value: sm.avg_ml_prob !== null ? pct(sm.avg_ml_prob) : "—",
      label: "Avg ML Probability",
      sub:   "success confidence",
    },
    {
      color: "amber",
      icon:  "🎯",
      value: sm.avg_final_score !== null ? num(sm.avg_final_score) : "—",
      label: "Avg Final Score",
      sub:   "0–100 scale",
    },
    {
      color: sm.model_available ? "green" : "rose",
      icon:  sm.model_available ? "📦" : "⚠️",
      value: modelVersion || (sm.model_available ? "trained" : "none"),
      label: "Model Version",
      sub:   sm.model_available ? "production model active" : "heuristic fallback",
    },
  ];

  grid.innerHTML = cards.map(c => `
    <div class="kpi-card ${c.color}">
      <span class="kpi-icon">${c.icon}</span>
      <span class="kpi-value">${c.value}</span>
      <span class="kpi-label">${c.label}</span>
      <span class="kpi-sub">${c.sub}</span>
    </div>
  `).join("");
}

// ── Alert banner ──────────────────────────────────────────────────────────────

function renderAlerts(alerts) {
  const banner = document.getElementById("ai-alert-banner");
  const list   = document.getElementById("ai-alert-list");
  if (!banner || !list) return;
  if (!alerts || alerts.length === 0) {
    banner.classList.remove("visible");
    return;
  }
  list.innerHTML = alerts.map(a => `<li>${a}</li>`).join("");
  banner.classList.add("visible");
}

// ── Chart: outcomes pie ───────────────────────────────────────────────────────

function renderOutcomesChart(sm) {
  const ctx = document.getElementById("chart-outcomes");
  if (!ctx) return;
  destroyChart(_chartOutcomes);

  const n         = sm.total_assignments || 0;
  const nOutcomes = Math.round((sm.success_rate !== null ? 1 : 0) * n); // rough
  const successes = sm.success_rate !== null ? Math.round(sm.success_rate * n) : 0;
  const delayed   = sm.delay_rate   !== null ? Math.round(sm.delay_rate   * n) : 0;
  const unknown   = Math.max(0, n - successes - delayed);

  _chartOutcomes = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Success", "Delayed", "No Outcome Yet"],
      datasets: [{
        data: [successes, delayed, unknown],
        backgroundColor: [C.green, C.amber, C.gray],
        borderWidth: 2,
        borderColor: "#fff",
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { font: { size: 11 }, boxWidth: 12 } },
      },
    },
  });
}

// ── Chart: workload bar ───────────────────────────────────────────────────────

function renderWorkloadChart(workload) {
  const ctx = document.getElementById("chart-workload");
  if (!ctx) return;
  destroyChart(_chartWorkload);

  const labels = (workload.employees || []).slice(0, 15);
  const active  = (workload.active   || []).slice(0, 15);
  const overdue = (workload.overdue  || []).slice(0, 15);

  _chartWorkload = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Active",
          data: active,
          backgroundColor: C.blue,
          borderRadius: 4,
        },
        {
          label: "Overdue",
          data: overdue,
          backgroundColor: C.rose,
          borderRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { font: { size: 11 }, boxWidth: 12 } } },
      scales: {
        x: { stacked: true, ticks: { font: { size: 10 } } },
        y: { stacked: true, beginAtZero: true, ticks: { stepSize: 1 } },
      },
    },
  });
}

// ── Chart: reason tags bar ────────────────────────────────────────────────────

function renderReasonTagsChart(reasonTags) {
  const ctx = document.getElementById("chart-reason-tags");
  if (!ctx) return;
  destroyChart(_chartReasonTags);

  const tagColors = [C.indigo, C.teal, C.blue, C.green, C.amber, C.purple, C.rose, C.gray];

  _chartReasonTags = new Chart(ctx, {
    type: "bar",
    data: {
      labels: reasonTags.tags || [],
      datasets: [{
        label: "Count",
        data: reasonTags.counts || [],
        backgroundColor: (reasonTags.tags || []).map((_, i) => tagColors[i % tagColors.length]),
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { beginAtZero: true, ticks: { stepSize: 1 } },
        y: { ticks: { font: { size: 11 } } },
      },
    },
  });
}

// ── Chart: ML probability histogram ──────────────────────────────────────────

function renderMlProbChart(recentAssignments) {
  const ctx = document.getElementById("chart-ml-prob");
  if (!ctx) return;
  destroyChart(_chartMlProb);

  // Bucket into 10 bins: [0–0.1), [0.1–0.2), … [0.9–1.0]
  const bins   = Array(10).fill(0);
  const labels = ["0–10%","10–20%","20–30%","30–40%","40–50%","50–60%","60–70%","70–80%","80–90%","90–100%"];
  (recentAssignments || []).forEach(r => {
    const p = r.ml_prob;
    if (p === null || p === undefined) return;
    const idx = Math.min(9, Math.floor(p * 10));
    bins[idx]++;
  });

  _chartMlProb = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Assignments",
        data: bins,
        backgroundColor: C.indigo,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { font: { size: 10 } } },
        y: { beginAtZero: true, ticks: { stepSize: 1 } },
      },
    },
  });
}

// ── Model registry table ──────────────────────────────────────────────────────

function renderModelRegistry(registry) {
  const wrap = document.getElementById("model-registry-wrap");
  if (!wrap) return;

  const badge = document.getElementById("ai-model-badge");
  if (badge) {
    badge.textContent = registry.current_version
      ? `current: ${registry.current_version}`
      : "no model";
  }

  if (!registry.versions || registry.versions.length === 0) {
    wrap.innerHTML = `<p style="color:var(--gray-400);font-size:13px;padding:12px 0">No model versions registered yet. Run <code>python scripts/retrain_model.py</code> to train.</p>`;
    return;
  }

  const rows = [...registry.versions].reverse().map(v => `
    <tr>
      <td>${v.version}</td>
      <td><span class="status-pill ${v.status}">${v.status}</span></td>
      <td>${v.auc ? v.auc.toFixed(4) : "—"}</td>
      <td>${v.accuracy ? v.accuracy.toFixed(4) : "—"}</td>
      <td>${v.f1 ? v.f1.toFixed(4) : "—"}</td>
      <td>${v.n_train ?? "—"}</td>
      <td style="color:var(--gray-400)">${v.trained_at || "—"}</td>
    </tr>
  `).join("");

  wrap.innerHTML = `
    <div style="overflow-x:auto">
      <table class="model-table">
        <thead>
          <tr>
            <th>Version</th><th>Status</th><th>AUC</th>
            <th>Accuracy</th><th>F1</th><th>Train Rows</th><th>Trained At</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

// ── Recent assignments table ──────────────────────────────────────────────────

function renderRecentAssignments(records) {
  const wrap  = document.getElementById("ai-assign-table-wrap");
  const badge = document.getElementById("ai-assign-count");
  if (!wrap) return;
  if (badge) badge.textContent = records.length;

  if (!records.length) {
    wrap.innerHTML = `<p style="padding:20px;color:var(--gray-400);font-size:13px">No assignment events found in the log.</p>`;
    return;
  }

  const rows = records.map(r => {
    const tags = (r.reason_tags || []).map(t => `<span class="tag-chip">${t}</span>`).join(" ");
    return `
      <tr>
        <td>${r.task_id ?? "—"}</td>
        <td>${r.employee_id ?? "—"}</td>
        <td>${r.final_score !== null && r.final_score !== undefined ? Number(r.final_score).toFixed(2) : "—"}</td>
        <td>${r.ml_prob !== null && r.ml_prob !== undefined ? pct(r.ml_prob) : "—"}</td>
        <td>${tags || "<span style='color:var(--gray-300)'>—</span>"}</td>
        <td style="color:var(--gray-400);white-space:nowrap">${r.timestamp || "—"}</td>
      </tr>
    `;
  }).join("");

  wrap.innerHTML = `
    <table class="assign-table">
      <thead>
        <tr>
          <th>Task</th><th>Employee</th><th>Score</th>
          <th>ML Prob</th><th>Reason Tags</th><th>Timestamp</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// ── Inference latency ─────────────────────────────────────────────────────────

function renderLatency(stats) {
  const wrap = document.getElementById("latency-wrap");
  if (!wrap) return;
  if (!stats || stats.n_samples === 0) {
    wrap.innerHTML = `<p style="color:var(--gray-400);font-size:13px">No batch prediction calls recorded yet.</p>`;
    return;
  }
  wrap.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
      ${[
        ["Samples", stats.n_samples],
        ["Avg (ms)", num(stats.avg_ms, 2)],
        ["P50 (ms)", num(stats.p50_ms, 2)],
        ["P95 (ms)", num(stats.p95_ms, 2)],
      ].map(([label, value]) => `
        <div style="background:var(--gray-50);border-radius:10px;padding:12px 14px">
          <div style="font-size:22px;font-weight:800;color:var(--an-indigo)">${value}</div>
          <div style="font-size:11px;color:var(--gray-500);margin-top:2px">${label}</div>
        </div>
      `).join("")}
    </div>
  `;
}

// ── Data quality ──────────────────────────────────────────────────────────────

function renderDataQuality(dq) {
  const wrap = document.getElementById("dq-wrap");
  if (!wrap) return;
  wrap.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:${dq.warnings && dq.warnings.length ? '8px' : '0'}">
      ${[
        ["Log Lines",    dq.total_log_lines],
        ["Assignments",  dq.assignment_events],
        ["Outcomes",     dq.outcome_events],
        ["Duplicates",   dq.duplicate_task_ids],
        ["Missing ML",   dq.missing_ml_prob],
      ].map(([label, value]) => `
        <div style="background:var(--gray-50);border-radius:10px;padding:10px 12px">
          <div style="font-size:20px;font-weight:800;color:var(--gray-800)">${value ?? "—"}</div>
          <div style="font-size:11px;color:var(--gray-500);margin-top:2px">${label}</div>
        </div>
      `).join("")}
    </div>
    ${dq.warnings && dq.warnings.length
      ? `<div class="dq-warn"><ul>${dq.warnings.map(w => `<li>${w}</li>`).join("")}</ul></div>`
      : ""}
  `;
}

// ── Main fetch & render ───────────────────────────────────────────────────────

async function loadAiData() {
  try {
    const resp = await fetch("/analytics/ai/data");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    const sm      = data.system_metrics      || {};
    const reg     = data.model_registry      || {};
    const wl      = data.workload            || {};
    const rt      = data.reason_tags         || {};
    const recent  = data.recent_assignments  || [];
    const stats   = data.inference_stats     || {};
    const dq      = data.data_quality        || {};

    renderAlerts(sm.alerts);
    renderKpis(sm, reg.current_version);
    renderOutcomesChart(sm);
    renderWorkloadChart(wl);
    renderReasonTagsChart(rt);
    renderMlProbChart(recent);
    renderModelRegistry(reg);
    renderRecentAssignments(recent);
    renderLatency(stats);
    renderDataQuality(dq);

    const chip = document.getElementById("ai-updated-text");
    if (chip) chip.textContent = "Updated " + new Date().toLocaleTimeString();

  } catch (err) {
    console.error("[analytics.js] fetch failed:", err);
    const chip = document.getElementById("ai-updated-text");
    if (chip) chip.textContent = "Refresh failed";
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  loadAiData();
  setInterval(loadAiData, 30_000);   // auto-refresh every 30 s
});

"""
Build self-contained GitHub Pages dashboard from parquet history.
Supports full light / dark mode via CSS custom properties + localStorage.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import (
    VINGROUP_TICKERS, VINGROUP_GROUP,
    TICKER_HIST_FILE, SECTOR_HIST_FILE,
    DOCS_DIR, DASHBOARD_FILE, JSON_FILE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _safe(val):
    if val is None:
        return None
    try:
        if np.isnan(val) or np.isinf(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (float, np.floating)):
        return round(float(val), 2)
    return val


def _records(df):
    return [{k: _safe(v) for k, v in row.items()} for row in df.to_dict("records")]


# ── Load data ─────────────────────────────────────────────────────────────────
def load_data():
    for p in (TICKER_HIST_FILE, SECTOR_HIST_FILE):
        if not Path(p).exists():
            print(f"ERROR: {p} not found. Run daily_compute.py first.")
            sys.exit(1)

    tick_h = pd.read_parquet(TICKER_HIST_FILE)
    sect_h = pd.read_parquet(SECTOR_HIST_FILE)
    tick_h["date"] = pd.to_datetime(tick_h["date"])
    sect_h["date"] = pd.to_datetime(sect_h["date"])

    latest_date = tick_h["date"].max()
    cutoff_30   = latest_date - timedelta(days=30)

    return (
        tick_h[tick_h["date"] == latest_date].copy(),
        tick_h[tick_h["date"] >= cutoff_30].copy(),
        sect_h[sect_h["date"] == latest_date].copy(),
        sect_h[sect_h["date"] >= cutoff_30].copy(),
        latest_date,
    )


# ── Build JSON payload ────────────────────────────────────────────────────────
def build_payload(tick_l, tick_30, sect_l, sect_30, latest_date):
    all_pe = tick_l["pe"].dropna()
    all_pb = tick_l["pb"].dropna()

    market = {
        "date":      latest_date.strftime("%Y-%m-%d"),
        "median_pe": _safe(all_pe.median()),
        "median_pb": _safe(all_pb.median()),
        "mean_pe":   _safe(all_pe.mean()),
        "mean_pb":   _safe(all_pb.mean()),
        "total":     len(tick_l),
        "valid_pe":  int(all_pe.notna().sum()),
        "valid_pb":  int(all_pb.notna().sum()),
    }

    vg = tick_l[tick_l["ticker"].isin(VINGROUP_TICKERS)][
        ["ticker", "close", "pe", "pb"]
    ].copy()
    vingroup = _records(vg)

    sect_cols = ["group","count","valid_pe","valid_pb",
                 "median_pe","median_pb","mean_pe","mean_pb",
                 "p25_pe","p75_pe","p25_pb","p75_pb"]
    avail = [c for c in sect_cols if c in sect_l.columns]
    sectors = _records(sect_l[avail].sort_values("median_pe", na_position="last"))

    top_groups = (
        sect_l.nlargest(8, "count")["group"].tolist() if not sect_l.empty else []
    )
    trend = {}
    for grp in top_groups:
        sub = (sect_30[sect_30["group"] == grp]
               .sort_values("date")[["date","median_pe","median_pb"]])
        trend[grp] = {
            "dates": sub["date"].dt.strftime("%Y-%m-%d").tolist(),
            "pe":    [_safe(v) for v in sub["median_pe"]],
            "pb":    [_safe(v) for v in sub["median_pb"]],
        }

    tbl_cols = ["ticker","close","pe","pb","sector","industry","group"]
    avail_t  = [c for c in tbl_cols if c in tick_l.columns]
    tickers  = _records(tick_l[avail_t].sort_values("pe", na_position="last"))

    return {
        "market":   market,
        "vingroup": vingroup,
        "sectors":  sectors,
        "trend":    trend,
        "tickers":  tickers,
    }


# ── HTML template (light / dark mode) ────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>VN-HOSE P/E & P/B Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css"/>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>

<style>
/* ── CSS custom properties: all colours go here ─────────────────────── */
:root {
  --bg:         #0f172a;
  --card:       #1e293b;
  --card2:      #0f172a;
  --border:     #334155;
  --text:       #e2e8f0;
  --muted:      #94a3b8;
  --dim:        #64748b;
  --accent:     #38bdf8;
  --accent2:    #818cf8;
  --green:      #4ade80;
  --yellow:     #facc15;
  --orange:     #fb923c;
  --red:        #f87171;
  --grid:       #1e3a5f;
  --hover:      #1e3a5f;
  --vg-bg:      #0f172a;
  --vg-border:  #1d4ed8;
  --btn-bg:     #1e293b;
  --btn-border: #475569;
  --shadow:     rgba(0,0,0,0.4);
}
[data-theme="light"] {
  --bg:         #f1f5f9;
  --card:       #ffffff;
  --card2:      #f8fafc;
  --border:     #e2e8f0;
  --text:       #1e293b;
  --muted:      #475569;
  --dim:        #94a3b8;
  --accent:     #0284c7;
  --accent2:    #6366f1;
  --green:      #16a34a;
  --yellow:     #b45309;
  --orange:     #c2410c;
  --red:        #dc2626;
  --grid:       #e2e8f0;
  --hover:      #eff6ff;
  --vg-bg:      #eff6ff;
  --vg-border:  #3b82f6;
  --btn-bg:     #ffffff;
  --btn-border: #cbd5e1;
  --shadow:     rgba(0,0,0,0.08);
}

/* ── Reset / base ─────────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
  font-size: 14px;
  line-height: 1.6;
  transition: background .25s, color .25s;
}
a { color: var(--accent); text-decoration: none; }

/* ── Layout ───────────────────────────────────────────────────────────── */
.page   { max-width: 1280px; margin: 0 auto; padding: 24px 16px 64px; }
.grid-4 { display: grid; grid-template-columns: repeat(2,1fr); gap: 14px; }
.grid-2 { display: grid; grid-template-columns: repeat(1,1fr); gap: 16px; }
@media(min-width:640px)  { .grid-4 { grid-template-columns: repeat(4,1fr); } }
@media(min-width:1024px) { .grid-2 { grid-template-columns: repeat(2,1fr); } }
.grid-vg { display: grid; grid-template-columns: repeat(2,1fr); gap: 12px; }
@media(min-width:640px)  { .grid-vg { grid-template-columns: repeat(4,1fr); } }

/* ── Card ─────────────────────────────────────────────────────────────── */
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 20px;
  box-shadow: 0 2px 8px var(--shadow);
  transition: background .25s, border-color .25s;
}

/* ── Header ───────────────────────────────────────────────────────────── */
.hdr {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 28px;
}
.hdr-left h1  { font-size: 1.75rem; font-weight: 800; line-height: 1.2; }
.hdr-left p   { color: var(--muted); font-size: .8rem; margin-top: 4px; }
.hdr-left .hl { color: var(--accent); font-weight: 600; }

/* ── Theme toggle button ─────────────────────────────────────────────── */
.theme-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--btn-border);
  border-radius: 8px;
  background: var(--btn-bg);
  color: var(--text);
  font-size: .8rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: background .2s, border-color .2s, color .2s;
  user-select: none;
}
.theme-btn:hover { border-color: var(--accent); color: var(--accent); }

/* ── Summary cards ───────────────────────────────────────────────────── */
.lbl  { color: var(--muted); font-size: .7rem; text-transform: uppercase;
        letter-spacing: .07em; margin-bottom: 6px; }
.big  { font-size: 2rem; font-weight: 800; color: var(--accent); }
.sub  { color: var(--dim); font-size: .75rem; margin-top: 4px; }

/* ── Section headings ────────────────────────────────────────────────── */
.sec-title {
  font-size: .95rem;
  font-weight: 700;
  margin-bottom: 4px;
}
.sec-sub { color: var(--muted); font-size: .75rem; margin-bottom: 16px; }

/* ── Vingroup cards ──────────────────────────────────────────────────── */
.vg-card {
  background: var(--vg-bg);
  border: 1px solid var(--vg-border);
  border-radius: 12px;
  padding: 14px;
  transition: background .25s, border-color .25s;
}
.vg-ticker { color: var(--accent); font-size: 1.2rem; font-weight: 800; }
.vg-row    { color: var(--muted); font-size: .8rem; margin-top: 5px; }
.vg-val    { font-weight: 700; }
.vg-badge  {
  display: inline-block;
  background: #1d4ed8;
  color: #fff;
  font-size: .65rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  margin-left: 6px;
  vertical-align: middle;
}

/* ── DataTable dark/light ────────────────────────────────────────────── */
table.dataTable {
  background: var(--card) !important;
  color: var(--text) !important;
  border-collapse: collapse;
  width: 100% !important;
}
table.dataTable thead th {
  background: var(--card2) !important;
  color: var(--muted) !important;
  border-bottom: 1px solid var(--border) !important;
  padding: 10px 12px !important;
  font-weight: 700;
  white-space: nowrap;
}
table.dataTable tbody td { padding: 8px 12px; border-bottom: 1px solid var(--border); }
table.dataTable tbody tr:hover td { background: var(--hover) !important; }
.dataTables_wrapper .dataTables_filter input,
.dataTables_wrapper .dataTables_length select {
  background: var(--card2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 5px 10px;
}
.dataTables_wrapper .dataTables_filter label,
.dataTables_wrapper .dataTables_length label,
.dataTables_wrapper .dataTables_info { color: var(--muted); }
.dataTables_wrapper .dataTables_paginate { margin-top: 10px; }
.dataTables_wrapper .paginate_button { color: var(--muted) !important; border-radius: 6px !important; padding: 4px 10px !important; }
.dataTables_wrapper .paginate_button.current { background: #1d4ed8 !important; color: #fff !important; }
.dataTables_wrapper .paginate_button:hover:not(.disabled) { background: var(--hover) !important; color: var(--text) !important; }

/* ── Misc ─────────────────────────────────────────────────────────────── */
.mb-4  { margin-bottom: 16px; }
.mb-6  { margin-bottom: 24px; }
.mb-8  { margin-bottom: 32px; }
.ovx   { overflow-x: auto; }
.footer { text-align: center; color: var(--dim); font-size: .75rem; margin-top: 48px; }
</style>
</head>

<body>
<div class="page">

  <!-- ── Header ──────────────────────────────────────────────────────────── -->
  <header class="hdr">
    <div class="hdr-left">
      <h1>🇻🇳 VN-HOSE P/E &amp; P/B</h1>
      <p>
        As of <span class="hl" id="hdr-date"></span>
        &nbsp;·&nbsp; Source: vnstock (KBS)
        &nbsp;·&nbsp; PE = Close / Annual EPS &nbsp; PB = Close / BVPS
      </p>
    </div>
    <button class="theme-btn" id="theme-btn" onclick="toggleTheme()">
      <span id="theme-icon">☀️</span>
      <span id="theme-label">Light mode</span>
    </button>
  </header>

  <!-- ── Market summary ─────────────────────────────────────────────────── -->
  <div class="grid-4 mb-8">
    <div class="card">
      <div class="lbl">HOSE Median P/E</div>
      <div class="big" id="mkt-pe">—</div>
      <div class="sub">All HOSE stocks</div>
    </div>
    <div class="card">
      <div class="lbl">HOSE Median P/B</div>
      <div class="big" id="mkt-pb">—</div>
      <div class="sub">All HOSE stocks</div>
    </div>
    <div class="card">
      <div class="lbl">Stocks with P/E</div>
      <div class="big" style="color:var(--text)" id="mkt-npe">—</div>
      <div class="sub" id="mkt-total"></div>
    </div>
    <div class="card">
      <div class="lbl">Stocks with P/B</div>
      <div class="big" style="color:var(--text)" id="mkt-npb">—</div>
      <div class="sub">Valid values</div>
    </div>
  </div>

  <!-- ── Vingroup Ecosystem ─────────────────────────────────────────────── -->
  <div class="card mb-8">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
      <span class="sec-title">🏙️ Vingroup Ecosystem</span>
      <span class="vg-badge">Special Group</span>
    </div>
    <div class="grid-vg" id="vg-cards"></div>
  </div>

  <!-- ── Sector charts ──────────────────────────────────────────────────── -->
  <div class="grid-2 mb-8">
    <div class="card">
      <div class="sec-title">📊 Sector Median P/E</div>
      <div class="sec-sub">Colour: green &lt;12 · blue &lt;20 · yellow &lt;30 · red ≥30</div>
      <canvas id="chart-pe"></canvas>
    </div>
    <div class="card">
      <div class="sec-title">📊 Sector Median P/B</div>
      <div class="sec-sub">Lower = cheaper relative to book value</div>
      <canvas id="chart-pb"></canvas>
    </div>
  </div>

  <!-- ── 30-day trend ───────────────────────────────────────────────────── -->
  <div class="card mb-8">
    <div class="sec-title">📈 30-Day Sector P/E Trend</div>
    <div class="sec-sub">Median P/E per sector · top 8 sectors by stock count</div>
    <canvas id="chart-trend" height="220"></canvas>
    <p id="trend-msg" style="color:var(--dim);font-size:.75rem;margin-top:8px"></p>
  </div>

  <!-- ── Full ticker table ──────────────────────────────────────────────── -->
  <div class="card">
    <div class="sec-title mb-4">📋 All HOSE Stocks</div>
    <div class="ovx">
      <table id="tbl" class="display compact nowrap" style="width:100%">
        <thead>
          <tr>
            <th>Ticker</th><th>Close (VND)</th>
            <th>P/E</th><th>P/B</th>
            <th>Sector</th><th>Industry</th><th>Group</th>
          </tr>
        </thead>
        <tbody id="tbl-body"></tbody>
      </table>
    </div>
  </div>

  <p class="footer">
    Auto-updated after market close (Mon–Fri) via GitHub Actions
    &nbsp;·&nbsp; vnstock (KBS) · Chart.js · DataTables
  </p>
</div><!-- /page -->

<script>
/* ════════════════════════════════════════════════════════════════════════
   DATA (injected by build_dashboard.py)
   ════════════════════════════════════════════════════════════════════════ */
const D = __DATA_JSON__;

/* ════════════════════════════════════════════════════════════════════════
   THEME ENGINE
   ════════════════════════════════════════════════════════════════════════ */
const STORAGE_KEY = 'vn-pe-pb-theme';
const charts      = {};   // chart references stored here after creation

function themeColors(theme) {
  const dk = theme === 'dark';
  return {
    grid:   dk ? '#1e3a5f' : '#e2e8f0',
    ticks:  dk ? '#94a3b8' : '#475569',
    legend: dk ? '#94a3b8' : '#475569',
  };
}

function applyChartTheme(theme) {
  const c = themeColors(theme);
  Object.values(charts).forEach(ch => {
    if (!ch) return;
    ['x','y'].forEach(ax => {
      const scale = ch.options.scales?.[ax];
      if (scale) {
        scale.grid.color  = c.grid;
        scale.ticks.color = c.ticks;
      }
    });
    const leg = ch.options.plugins?.legend?.labels;
    if (leg) leg.color = c.legend;
    ch.update('none');
  });
}

function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem(STORAGE_KEY, theme);
  const dk = theme === 'dark';
  document.getElementById('theme-icon').textContent  = dk ? '☀️' : '🌙';
  document.getElementById('theme-label').textContent = dk ? 'Light mode' : 'Dark mode';
  applyChartTheme(theme);
}

function toggleTheme() {
  const curr = document.documentElement.getAttribute('data-theme');
  setTheme(curr === 'dark' ? 'light' : 'dark');
}

// Detect initial theme BEFORE charts render (avoids flash)
const _saved = localStorage.getItem(STORAGE_KEY);
const _sysDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
const INIT_THEME = _saved || (_sysDark ? 'dark' : 'light');
document.documentElement.setAttribute('data-theme', INIT_THEME);
// Sync button label once DOM is ready (set below after DOMContentLoaded)

/* ════════════════════════════════════════════════════════════════════════
   COLOUR HELPERS
   ════════════════════════════════════════════════════════════════════════ */
const PALETTE = [
  '#38bdf8','#818cf8','#34d399','#fbbf24','#f87171',
  '#a78bfa','#fb923c','#4ade80','#f472b6','#2dd4bf',
  '#c084fc','#fcd34d','#6ee7b7','#fca5a5','#94a3b8',
];

function peCol(v) {
  const dk = document.documentElement.getAttribute('data-theme') === 'dark';
  if (v == null) return dk ? '#475569' : '#94a3b8';
  if (v < 12)   return dk ? '#4ade80' : '#16a34a';
  if (v < 20)   return dk ? '#38bdf8' : '#0284c7';
  if (v < 30)   return dk ? '#facc15' : '#b45309';
  return              dk ? '#f87171' : '#dc2626';
}

function fmt(v, dp=2)  { return v == null ? '—' : (+v).toFixed(dp); }
function fmtK(v)       { return v == null ? '—' : (v/1000).toFixed(1)+'K'; }

/* ════════════════════════════════════════════════════════════════════════
   DOM POPULATION
   ════════════════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {

  // Sync theme toggle button text now that DOM exists
  const dk0 = INIT_THEME === 'dark';
  document.getElementById('theme-icon').textContent  = dk0 ? '☀️' : '🌙';
  document.getElementById('theme-label').textContent = dk0 ? 'Light mode' : 'Dark mode';

  // ── Header
  const m = D.market;
  document.getElementById('hdr-date').textContent  = m.date;
  document.getElementById('mkt-pe').textContent    = fmt(m.median_pe);
  document.getElementById('mkt-pb').textContent    = fmt(m.median_pb);
  document.getElementById('mkt-npe').textContent   = m.valid_pe;
  document.getElementById('mkt-npb').textContent   = m.valid_pb;
  document.getElementById('mkt-total').textContent = `of ${m.total} listed`;

  // ── Vingroup cards
  const vgEl = document.getElementById('vg-cards');
  D.vingroup.forEach(v => {
    const div = document.createElement('div');
    div.className = 'vg-card';
    div.innerHTML = `
      <div class="vg-ticker">${v.ticker}</div>
      <div class="vg-row">Close: <span class="vg-val">${fmtK(v.close)}</span></div>
      <div class="vg-row">P/E:&nbsp; <span class="vg-val" style="color:${peCol(v.pe)}">${fmt(v.pe)}</span></div>
      <div class="vg-row">P/B:&nbsp; <span class="vg-val" style="color:var(--accent2)">${fmt(v.pb)}</span></div>`;
    vgEl.appendChild(div);
  });

  // ── Sector P/E chart
  const tc = themeColors(INIT_THEME);
  const sects  = D.sectors.filter(s => s.median_pe != null);
  const sLabels = sects.map(s => s.group);
  const sPe     = sects.map(s => s.median_pe);

  const barBase = (axis) => ({
    indexAxis: 'y',
    responsive: true,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: tc.grid }, ticks: { color: tc.ticks } },
      y: { grid: { color: tc.grid }, ticks: { color: tc.ticks, font: { size: 11 } } },
    },
  });

  charts.pe = new Chart(document.getElementById('chart-pe'), {
    type: 'bar',
    data: {
      labels: sLabels,
      datasets: [{ data: sPe, backgroundColor: sPe.map(peCol), borderRadius: 5 }],
    },
    options: {
      ...barBase(),
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` P/E: ${ctx.parsed.x.toFixed(1)}` } },
      },
    },
  });

  // ── Sector P/B chart
  const sPb  = D.sectors.filter(s => s.median_pb != null);
  charts.pb  = new Chart(document.getElementById('chart-pb'), {
    type: 'bar',
    data: {
      labels: sPb.map(s => s.group),
      datasets: [{ data: sPb.map(s => s.median_pb), backgroundColor: 'var(--accent2)', borderRadius: 5 }],
    },
    options: {
      ...barBase(),
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` P/B: ${ctx.parsed.x.toFixed(2)}` } },
      },
    },
  });

  // ── 30-day trend
  const tGroups = Object.keys(D.trend);
  if (tGroups.length > 0) {
    charts.trend = new Chart(document.getElementById('chart-trend'), {
      type: 'line',
      data: {
        datasets: tGroups.map((grp, i) => ({
          label: grp,
          data: D.trend[grp].dates.map((d, j) => ({ x: d, y: D.trend[grp].pe[j] })),
          borderColor: PALETTE[i % PALETTE.length],
          backgroundColor: 'transparent',
          tension: 0.35,
          pointRadius: 3,
          borderWidth: 2,
        })),
      },
      options: {
        responsive: true,
        parsing: false,
        scales: {
          x: {
            type: 'category',
            ticks: { color: tc.ticks, maxRotation: 45, autoSkip: true, maxTicksLimit: 15 },
            grid: { color: tc.grid },
          },
          y: {
            title: { display: true, text: 'Median P/E', color: tc.ticks },
            grid: { color: tc.grid },
            ticks: { color: tc.ticks },
          },
        },
        plugins: {
          legend: { labels: { color: tc.legend, font: { size: 11 }, boxWidth: 14 } },
        },
      },
    });
  } else {
    document.getElementById('trend-msg').textContent =
      'Trend appears after the second trading day of data collection.';
  }

  // ── Ticker table
  const tbody = document.getElementById('tbl-body');
  D.tickers.forEach(t => {
    const isVG  = ['VIC','VHM','VRE','VPL'].includes(t.ticker);
    const tkrHtml = isVG
      ? `<span style="color:var(--accent);font-weight:700">${t.ticker}</span> <span style="color:#3b82f6;font-size:.65rem">VG</span>`
      : `<span style="font-weight:600">${t.ticker}</span>`;
    const peHtml  = t.pe == null ? '—'
      : `<span style="color:${peCol(t.pe)};font-weight:700">${fmt(t.pe)}</span>`;
    const pbHtml  = t.pb == null ? '—'
      : `<span style="color:var(--accent2);font-weight:700">${fmt(t.pb)}</span>`;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${tkrHtml}</td>
      <td>${fmtK(t.close)}</td>
      <td>${peHtml}</td>
      <td>${pbHtml}</td>
      <td style="color:var(--muted)">${t.sector||'—'}</td>
      <td style="color:var(--dim);font-size:.8rem">${t.industry||'—'}</td>
      <td style="color:var(--muted)">${t.group||'—'}</td>`;
    tbody.appendChild(tr);
  });

  $('#tbl').DataTable({
    pageLength: 25,
    order: [[2,'asc']],
    columnDefs: [{ targets:[1,2,3], type:'num' }],
    language: { search: 'Filter:', lengthMenu: 'Show _MENU_ stocks' },
  });

  // ── Apply chart theme after all charts created
  applyChartTheme(INIT_THEME);

}); // DOMContentLoaded
</script>
</body>
</html>"""


# ── Build entry-point ─────────────────────────────────────────────────────────
def build():
    Path(DOCS_DIR).mkdir(parents=True, exist_ok=True)

    tick_l, tick_30, sect_l, sect_30, latest_date = load_data()
    payload = build_payload(tick_l, tick_30, sect_l, sect_30, latest_date)

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"JSON saved → {JSON_FILE}")

    html = HTML.replace("__DATA_JSON__",
                        json.dumps(payload, ensure_ascii=False, default=str))
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard built → {DASHBOARD_FILE}")


if __name__ == "__main__":
    build()

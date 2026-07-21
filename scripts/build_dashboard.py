"""
Build self-contained HTML dashboard for GitHub Pages.
Reads data/ticker_history.parquet and data/sector_history.parquet,
serialises them as embedded JSON, and writes docs/index.html.

The output HTML has zero external build dependencies:
  - Tailwind CSS (CDN)
  - Chart.js 4 (CDN)
  - DataTables (CDN, jQuery bundled)
Everything renders without a build step — just open the HTML file.
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
    """Convert NaN / inf to None for JSON, round floats to 2dp."""
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


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    return [{k: _safe(v) for k, v in row.items()} for row in df.to_dict("records")]


# ── Data loading ──────────────────────────────────────────────────────────────
def load_data() -> tuple:
    for path in (TICKER_HIST_FILE, SECTOR_HIST_FILE):
        if not Path(path).exists():
            print(f"ERROR: {path} not found. Run daily_compute.py first.")
            sys.exit(1)

    tick_h = pd.read_parquet(TICKER_HIST_FILE)
    sect_h = pd.read_parquet(SECTOR_HIST_FILE)

    tick_h["date"] = pd.to_datetime(tick_h["date"])
    sect_h["date"] = pd.to_datetime(sect_h["date"])

    latest_date = tick_h["date"].max()
    cutoff_30   = latest_date - timedelta(days=30)

    tick_latest  = tick_h[tick_h["date"] == latest_date].copy()
    tick_30      = tick_h[tick_h["date"] >= cutoff_30].copy()
    sect_latest  = sect_h[sect_h["date"] == latest_date].copy()
    sect_30      = sect_h[sect_h["date"] >= cutoff_30].copy()

    return tick_latest, tick_30, sect_latest, sect_30, latest_date


# ── JSON payload ──────────────────────────────────────────────────────────────
def build_payload(tick_latest, tick_30, sect_latest, sect_30, latest_date) -> dict:
    date_str = latest_date.strftime("%Y-%m-%d")

    # Market summary
    all_pe = tick_latest["pe"].dropna()
    all_pb = tick_latest["pb"].dropna()
    market = {
        "date":          date_str,
        "median_pe":     _safe(all_pe.median()),
        "median_pb":     _safe(all_pb.median()),
        "mean_pe":       _safe(all_pe.mean()),
        "mean_pb":       _safe(all_pb.mean()),
        "total":         len(tick_latest),
        "valid_pe":      int(all_pe.notna().sum()),
        "valid_pb":      int(all_pb.notna().sum()),
    }

    # Vingroup cards
    vg = (
        tick_latest[tick_latest["ticker"].isin(VINGROUP_TICKERS)]
        [["ticker", "close", "pe", "pb"]]
        .copy()
    )
    vingroup = _df_to_records(vg)

    # Sector table (sorted by median_pe ascending)
    sect_cols = ["group", "count", "valid_pe", "valid_pb",
                 "median_pe", "median_pb", "mean_pe", "mean_pb",
                 "p25_pe", "p75_pe", "p25_pb", "p75_pb"]
    sect_avail = [c for c in sect_cols if c in sect_latest.columns]
    sectors = _df_to_records(
        sect_latest[sect_avail].sort_values("median_pe", na_position="last")
    )

    # 30-day trend: top 8 groups by count (for readability)
    top_groups = (
        sect_latest.nlargest(8, "count")["group"].tolist()
        if not sect_latest.empty else []
    )
    trend = {}
    for grp in top_groups:
        sub = (
            sect_30[sect_30["group"] == grp]
            .sort_values("date")[["date", "median_pe", "median_pb"]]
        )
        trend[grp] = {
            "dates": sub["date"].dt.strftime("%Y-%m-%d").tolist(),
            "pe":    [_safe(v) for v in sub["median_pe"]],
            "pb":    [_safe(v) for v in sub["median_pb"]],
        }

    # Full ticker table (sorted by PE asc, NaN last)
    tbl_cols = ["ticker", "close", "pe", "pb", "sector", "industry", "group"]
    tbl_avail = [c for c in tbl_cols if c in tick_latest.columns]
    tickers = _df_to_records(
        tick_latest[tbl_avail].sort_values("pe", na_position="last")
    )

    return {
        "market":   market,
        "vingroup": vingroup,
        "sectors":  sectors,
        "trend":    trend,
        "tickers":  tickers,
    }


# ── HTML template ─────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>VN-HOSE P/E & P/B Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css"/>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
<style>
  *{box-sizing:border-box}
  body{background:#0f172a;color:#e2e8f0;font-family:ui-sans-serif,system-ui,sans-serif;margin:0;padding:0}
  .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:1.5rem}
  .lbl{color:#94a3b8;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;margin-bottom:.25rem}
  .big{font-size:2rem;font-weight:700;color:#f1f5f9}
  .sub{color:#64748b;font-size:.78rem;margin-top:.2rem}
  .sky{color:#38bdf8}
  .vg-badge{background:#1d4ed8;color:#fff;padding:2px 8px;border-radius:9999px;font-size:.68rem;font-weight:600}
  /* DataTable overrides */
  table.dataTable{background:#1e293b;color:#e2e8f0;border-collapse:collapse;width:100%!important}
  table.dataTable thead th{background:#0f172a;color:#94a3b8;border-bottom:1px solid #334155;padding:10px 12px;font-weight:600;white-space:nowrap}
  table.dataTable tbody td{padding:8px 12px;border-bottom:1px solid #1e293b}
  table.dataTable tbody tr:hover{background:#1e3a5f!important}
  .dataTables_wrapper .dataTables_filter input,.dataTables_wrapper .dataTables_length select
    {background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:4px 8px}
  .dataTables_wrapper .dataTables_info,.dataTables_wrapper .dataTables_paginate{color:#64748b;margin-top:.5rem}
  .dataTables_wrapper .paginate_button{color:#94a3b8!important;padding:4px 10px!important;border-radius:6px!important}
  .dataTables_wrapper .paginate_button.current{background:#1d4ed8!important;color:#fff!important}
</style>
</head>
<body class="p-4 md:p-8">
<div class="max-w-7xl mx-auto">

  <!-- Header -->
  <div class="mb-8">
    <h1 class="text-3xl font-bold">🇻🇳 VN-HOSE P/E &amp; P/B Dashboard</h1>
    <p class="text-slate-400 mt-1 text-sm">
      As of <span class="sky font-semibold" id="hdr-date"></span>
      &nbsp;·&nbsp;Source: vnstock (KBS)
      &nbsp;·&nbsp;PE = Close&thinsp;/&thinsp;Annual EPS&nbsp;&nbsp;PB = Close&thinsp;/&thinsp;BVPS
    </p>
  </div>

  <!-- Market summary cards -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
    <div class="card"><div class="lbl">HOSE Median P/E</div><div class="big sky" id="mkt-pe">—</div><div class="sub">All HOSE stocks</div></div>
    <div class="card"><div class="lbl">HOSE Median P/B</div><div class="big sky" id="mkt-pb">—</div><div class="sub">All HOSE stocks</div></div>
    <div class="card"><div class="lbl">Stocks with P/E</div><div class="big" id="mkt-npe">—</div><div class="sub" id="mkt-total"></div></div>
    <div class="card"><div class="lbl">Stocks with P/B</div><div class="big" id="mkt-npb">—</div><div class="sub">Valid values</div></div>
  </div>

  <!-- Vingroup ecosystem -->
  <div class="card mb-8">
    <div class="flex items-center gap-3 mb-4">
      <h2 class="text-lg font-semibold">🏙️ Vingroup Ecosystem</h2>
      <span class="vg-badge">Special Group</span>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4" id="vg-cards"></div>
  </div>

  <!-- Sector charts row -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
    <div class="card">
      <h2 class="text-base font-semibold mb-4">📊 Sector Median P/E</h2>
      <canvas id="chart-pe"></canvas>
    </div>
    <div class="card">
      <h2 class="text-base font-semibold mb-4">📊 Sector Median P/B</h2>
      <canvas id="chart-pb"></canvas>
    </div>
  </div>

  <!-- 30-day trend -->
  <div class="card mb-8">
    <h2 class="text-base font-semibold">📈 30-Day P/E Trend — Top Sectors by Stock Count</h2>
    <p class="text-slate-400 text-xs mb-4">Median P/E per sector group per trading day</p>
    <canvas id="chart-trend" height="220"></canvas>
    <p class="text-slate-600 text-xs mt-3" id="trend-msg"></p>
  </div>

  <!-- Full ticker table -->
  <div class="card">
    <h2 class="text-base font-semibold mb-4">📋 All HOSE Stocks</h2>
    <div class="overflow-x-auto">
      <table id="tbl" class="display compact nowrap w-full text-sm">
        <thead>
          <tr>
            <th>Ticker</th><th>Close (VND)</th><th>P/E</th><th>P/B</th>
            <th>Sector</th><th>Industry</th><th>Group</th>
          </tr>
        </thead>
        <tbody id="tbl-body"></tbody>
      </table>
    </div>
  </div>

  <p class="text-center text-slate-600 text-xs mt-8">
    Auto-updated after market close (Mon–Fri) via GitHub Actions &nbsp;·&nbsp;
    Built with vnstock &amp; Chart.js
  </p>
</div>

<script>
const D = __DATA_JSON__;

// ── colour helpers ────────────────────────────────────────────────────────────
const PALETTE = ['#38bdf8','#818cf8','#34d399','#fbbf24','#f87171',
                 '#a78bfa','#fb923c','#4ade80','#f472b6','#2dd4bf',
                 '#c084fc','#fcd34d','#6ee7b7','#fca5a5','#94a3b8'];

function peCol(v) {
  if (v == null) return '#64748b';
  return v < 12 ? '#4ade80' : v < 20 ? '#38bdf8' : v < 30 ? '#facc15' : '#f87171';
}

function fmt(v, dp=2) {
  if (v == null || v === undefined) return '—';
  return (+v).toFixed(dp);
}

function fmtK(v) {
  if (v == null) return '—';
  return (v/1000).toFixed(1) + 'K';
}

// ── Header & summary cards ────────────────────────────────────────────────────
const m = D.market;
document.getElementById('hdr-date').textContent  = m.date;
document.getElementById('mkt-pe').textContent    = fmt(m.median_pe);
document.getElementById('mkt-pb').textContent    = fmt(m.median_pb);
document.getElementById('mkt-npe').textContent   = m.valid_pe;
document.getElementById('mkt-npb').textContent   = m.valid_pb;
document.getElementById('mkt-total').textContent = `of ${m.total} listed`;

// ── Vingroup cards ────────────────────────────────────────────────────────────
const vgEl = document.getElementById('vg-cards');
D.vingroup.forEach(v => {
  vgEl.innerHTML += `
    <div style="background:#0f172a;border:1px solid #1d4ed8;border-radius:10px;padding:1rem">
      <div style="color:#38bdf8;font-size:1.25rem;font-weight:700">${v.ticker}</div>
      <div style="color:#94a3b8;font-size:.8rem;margin-top:.4rem">
        Close: <span style="color:#f1f5f9;font-weight:600">${fmtK(v.close)}</span>
      </div>
      <div style="color:#94a3b8;font-size:.8rem">
        P/E: <span style="color:${peCol(v.pe)};font-weight:600">${fmt(v.pe)}</span>
      </div>
      <div style="color:#94a3b8;font-size:.8rem">
        P/B: <span style="color:#a78bfa;font-weight:600">${fmt(v.pb)}</span>
      </div>
    </div>`;
});

// ── Sector bar charts ─────────────────────────────────────────────────────────
const sectors   = D.sectors.filter(s => s.median_pe != null);
const sLabels   = sectors.map(s => s.group);
const sPe       = sectors.map(s => s.median_pe);
const sPb       = D.sectors.filter(s => s.median_pb != null);

const barOpts = (label, unit) => ({
  indexAxis: 'y',
  responsive: true,
  plugins: {
    legend: { display: false },
    tooltip: { callbacks: { label: ctx => ` ${label}: ${ctx.parsed.x.toFixed(2)}` } }
  },
  scales: {
    x: { grid: { color: '#1e3a5f' }, ticks: { color: '#94a3b8' } },
    y: { grid: { color: '#1e3a5f' }, ticks: { color: '#94a3b8', font: { size: 11 } } }
  }
});

new Chart(document.getElementById('chart-pe'), {
  type: 'bar',
  data: {
    labels: sLabels,
    datasets: [{ data: sPe, backgroundColor: sPe.map(peCol), borderRadius: 5 }]
  },
  options: barOpts('P/E', 'x')
});

new Chart(document.getElementById('chart-pb'), {
  type: 'bar',
  data: {
    labels: sPb.map(s => s.group),
    datasets: [{ data: sPb.map(s => s.median_pb), backgroundColor: '#818cf8', borderRadius: 5 }]
  },
  options: barOpts('P/B', 'x')
});

// ── 30-day trend ──────────────────────────────────────────────────────────────
const trendGroups = Object.keys(D.trend);
if (trendGroups.length > 0) {
  new Chart(document.getElementById('chart-trend'), {
    type: 'line',
    data: {
      datasets: trendGroups.map((grp, i) => ({
        label: grp,
        data: D.trend[grp].dates.map((d, j) => ({ x: d, y: D.trend[grp].pe[j] })),
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: 'transparent',
        tension: 0.35,
        pointRadius: 3,
        borderWidth: 2,
      }))
    },
    options: {
      responsive: true,
      parsing: false,
      scales: {
        x: { type: 'category',
             ticks: { color: '#94a3b8', maxRotation: 45, autoSkip: true, maxTicksLimit: 15 },
             grid: { color: '#1e3a5f' } },
        y: { title: { display: true, text: 'Median P/E', color: '#64748b' },
             grid: { color: '#1e3a5f' }, ticks: { color: '#94a3b8' } }
      },
      plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 }, boxWidth: 14 } } }
    }
  });
} else {
  document.getElementById('trend-msg').textContent =
    'Trend appears after the second day of data collection.';
}

// ── Ticker table ──────────────────────────────────────────────────────────────
const tbody = document.getElementById('tbl-body');
D.tickers.forEach(t => {
  const isVG = ['VIC','VHM','VRE','VPL'].includes(t.ticker);
  const pe   = t.pe  == null ? '—' : `<span style="color:${peCol(t.pe)};font-weight:600">${fmt(t.pe)}</span>`;
  const pb   = t.pb  == null ? '—' : `<span style="color:#a78bfa;font-weight:600">${fmt(t.pb)}</span>`;
  const tkr  = isVG
    ? `<span style="color:#38bdf8;font-weight:700">${t.ticker}</span> <span style="color:#1d4ed8;font-size:.65rem">VG</span>`
    : `<span style="font-weight:600">${t.ticker}</span>`;
  tbody.innerHTML += `<tr>
    <td>${tkr}</td>
    <td>${fmtK(t.close)}</td>
    <td>${pe}</td>
    <td>${pb}</td>
    <td style="color:#94a3b8">${t.sector||'—'}</td>
    <td style="color:#64748b;font-size:.8rem">${t.industry||'—'}</td>
    <td style="color:#94a3b8">${t.group||'—'}</td>
  </tr>`;
});

$(function(){
  $('#tbl').DataTable({
    pageLength: 25,
    order: [[2,'asc']],
    columnDefs: [{ targets:[1,2,3], type:'num' }],
    language: { search: 'Filter:', lengthMenu: 'Show _MENU_ stocks' }
  });
});
</script>
</body>
</html>"""


# ── Builder entry-point ────────────────────────────────────────────────────────
def build():
    Path(DOCS_DIR).mkdir(parents=True, exist_ok=True)

    tick_latest, tick_30, sect_latest, sect_30, latest_date = load_data()
    payload = build_payload(tick_latest, tick_30, sect_latest, sect_30, latest_date)

    # Write JSON sidecar (useful for external tooling)
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"JSON saved → {JSON_FILE}")

    # Inject data into HTML template
    html = HTML_TEMPLATE.replace(
        "__DATA_JSON__",
        json.dumps(payload, ensure_ascii=False, default=str),
    )
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard built → {DASHBOARD_FILE}")


if __name__ == "__main__":
    build()

"""
Build self-contained GitHub Pages dashboard from parquet history.
Supports full light / dark mode via CSS custom properties + localStorage.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import (
    VINGROUP_TICKERS, VINGROUP_GROUP,
    TICKER_HIST_FILE, SECTOR_HIST_FILE, FUND_FILE,
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

    if Path(FUND_FILE).exists():
        fund = pd.read_parquet(FUND_FILE)
        if "ticker" in fund.columns:
            fund_cols = ["ticker"]
            for c in ["eps_ttm", "bvps", "shares"]:
                if c in fund.columns and c not in tick_h.columns:
                    fund_cols.append(c)
            if len(fund_cols) > 1:
                tick_h = tick_h.merge(fund[fund_cols], on="ticker", how="left")
        else:
            fund_copy = fund.reset_index()
            fund_cols = ["ticker"]
            for c in ["eps_ttm", "bvps", "shares"]:
                if c in fund_copy.columns and c not in tick_h.columns:
                    fund_cols.append(c)
            if len(fund_cols) > 1:
                tick_h = tick_h.merge(fund_copy[fund_cols], on="ticker", how="left")

    if "ticker" in tick_h.columns:
        tick_h["ticker"] = tick_h["ticker"].astype(str).str.upper().str.strip()
        tick_h = tick_h[tick_h["ticker"].str.len() == 3]
        tick_h = tick_h.drop_duplicates(subset=["date", "ticker"], keep="last")
    if "group" in sect_h.columns:
        sect_h = sect_h.drop_duplicates(subset=["date", "group"], keep="last")

    latest_date = tick_h["date"].max()
    cutoff_5y   = latest_date - timedelta(days=365*5 + 10)

    return (
        tick_h[tick_h["date"] == latest_date].copy(),
        tick_h[tick_h["date"] >= cutoff_5y].copy(),
        sect_h[sect_h["date"] == latest_date].copy(),
        sect_h[sect_h["date"] >= cutoff_5y].copy(),
        latest_date,
    )


# ── Build JSON payload ────────────────────────────────────────────────────────
def build_payload(tick_l, tick_5y, sect_l, sect_5y, latest_date):
    tick_l = tick_l.drop_duplicates(subset=["ticker"], keep="first")
    if "shares" not in tick_l.columns:
        tick_l["shares"] = np.nan
    tick_l["shares"] = pd.to_numeric(tick_l["shares"], errors="coerce").fillna(0)
    if "eps_ttm" not in tick_l.columns:
        tick_l["eps_ttm"] = np.nan
    tick_l["eps_ttm"] = pd.to_numeric(tick_l["eps_ttm"], errors="coerce")
    if "bvps" not in tick_l.columns:
        tick_l["bvps"] = np.nan
    tick_l["bvps"] = pd.to_numeric(tick_l["bvps"], errors="coerce")
    tick_l["close"] = pd.to_numeric(tick_l["close"], errors="coerce")

    all_pe = tick_l["pe"].dropna()
    all_pb = tick_l["pb"].dropna()

    pe_val_m = tick_l[tick_l["pe"].notna() & (tick_l["shares"] > 0)]
    w_pe_m = (pe_val_m["close"] * pe_val_m["shares"]).sum() / (pe_val_m["eps_ttm"] * pe_val_m["shares"]).sum() if len(pe_val_m) > 0 and (pe_val_m["eps_ttm"] * pe_val_m["shares"]).sum() > 0 else np.nan
    pb_val_m = tick_l[tick_l["pb"].notna() & (tick_l["shares"] > 0)]
    w_pb_m = (pb_val_m["close"] * pb_val_m["shares"]).sum() / (pb_val_m["bvps"] * pb_val_m["shares"]).sum() if len(pb_val_m) > 0 and (pb_val_m["bvps"] * pb_val_m["shares"]).sum() > 0 else np.nan

    market = {
        "date":        latest_date.strftime("%Y-%m-%d"),
        "median_pe":   _safe(all_pe.median()),
        "median_pb":   _safe(all_pb.median()),
        "mean_pe":     _safe(all_pe.mean()),
        "mean_pb":     _safe(all_pb.mean()),
        "weighted_pe": _safe(w_pe_m),
        "weighted_pb": _safe(w_pb_m),
        "total":       len(tick_l),
        "valid_pe":    int(all_pe.count()),
        "valid_pb":    int(all_pb.count()),
        "p25_pe":      _safe(all_pe.quantile(.25)),
        "p75_pe":      _safe(all_pe.quantile(.75)),
        "p25_pb":      _safe(all_pb.quantile(.25)),
        "p75_pb":      _safe(all_pb.quantile(.75)),
    }

    vg_l = tick_l[tick_l["group"] == VINGROUP_GROUP]
    vg_pe = vg_l["pe"].dropna()
    vg_pb = vg_l["pb"].dropna()
    vg_pe_val = vg_l[vg_l["pe"].notna() & (vg_l["shares"] > 0)]
    vg_w_pe = (vg_pe_val["close"] * vg_pe_val["shares"]).sum() / (vg_pe_val["eps_ttm"] * vg_pe_val["shares"]).sum() if len(vg_pe_val) > 0 and (vg_pe_val["eps_ttm"] * vg_pe_val["shares"]).sum() > 0 else np.nan
    vg_pb_val = vg_l[vg_l["pb"].notna() & (vg_l["shares"] > 0)]
    vg_w_pb = (vg_pb_val["close"] * vg_pb_val["shares"]).sum() / (vg_pb_val["bvps"] * vg_pb_val["shares"]).sum() if len(vg_pb_val) > 0 and (vg_pb_val["bvps"] * vg_pb_val["shares"]).sum() > 0 else np.nan

    vingroup = {
        "median_pe":   _safe(vg_pe.median()),
        "median_pb":   _safe(vg_pb.median()),
        "weighted_pe": _safe(vg_w_pe),
        "weighted_pb": _safe(vg_w_pb),
        "valid_pe":    int(vg_pe.count()),
        "valid_pb":    int(vg_pb.count()),
    }

    raw_groups = [g for g in sect_l["group"].unique() if g != "VN-Index" and g != VINGROUP_GROUP]
    priority   = ["Ngân hàng","Bất động sản","Tài chính","Dịch vụ Tiêu dùng",
                  "Xây dựng và Vật liệu","Công nghiệp","Hàng Tiêu dùng","Dược phẩm và Y tế"]
    all_groups = [g for g in priority if g in raw_groups] + [g for g in raw_groups if g not in priority]
    if VINGROUP_GROUP in sect_l["group"].values:
        all_groups.append(VINGROUP_GROUP)

    sectors = _records(
        sect_l[sect_l["group"] != "VN-Index"]
        .sort_values("median_pe", na_position="last")
    )

    # Precompute daily pe_mc, pe_ern, pb_mc, pb_bv per group right from tick_5y
    tick_5y_c = tick_5y.copy()
    tick_5y_c["shares"] = pd.to_numeric(tick_5y_c["shares"], errors="coerce").fillna(0)
    tick_5y_c["close"] = pd.to_numeric(tick_5y_c["close"], errors="coerce")
    tick_5y_c["pe"] = pd.to_numeric(tick_5y_c.get("pe", np.nan), errors="coerce")
    tick_5y_c["pb"] = pd.to_numeric(tick_5y_c.get("pb", np.nan), errors="coerce")
    mc_5y = tick_5y_c["close"] * tick_5y_c["shares"]
    pe_valid = tick_5y_c["pe"].notna() & (tick_5y_c["pe"] > 0) & (tick_5y_c["shares"] > 0)
    pb_valid = tick_5y_c["pb"].notna() & (tick_5y_c["pb"] > 0) & (tick_5y_c["shares"] > 0)
    tick_5y_c["pe_mc"] = np.where(pe_valid, mc_5y, 0.0)
    tick_5y_c["pe_ern"] = np.where(pe_valid, mc_5y / tick_5y_c["pe"], 0.0)
    tick_5y_c["pb_mc"] = np.where(pb_valid, mc_5y, 0.0)
    tick_5y_c["pb_bv"] = np.where(pb_valid, mc_5y / tick_5y_c["pb"], 0.0)

    vni_sums = tick_5y_c.groupby("date")[["pe_mc", "pe_ern", "pb_mc", "pb_bv"]].sum().reset_index()
    grp_sums = tick_5y_c.groupby(["date", "group"])[["pe_mc", "pe_ern", "pb_mc", "pb_bv"]].sum().reset_index()

    # ── VN-Index (full market) daily median & weighted P/E & P/B ───────────
    vni_sect = sect_5y[sect_5y["group"] == "VN-Index"].sort_values("date")
    if not vni_sect.empty:
        vni_sect = vni_sect.merge(vni_sums, on="date", how="left")
        trend = {
            "VN-Index": {
                "dates": vni_sect["date"].dt.strftime("%Y-%m-%d").tolist(),
                "pe":    [_safe(v) for v in vni_sect["median_pe"]],
                "pb":    [_safe(v) for v in vni_sect["median_pb"]],
                "w_pe":  [_safe(v) for v in vni_sect.get("weighted_pe", pd.Series([np.nan]*len(vni_sect)))],
                "w_pb":  [_safe(v) for v in vni_sect.get("weighted_pb", pd.Series([np.nan]*len(vni_sect)))],
                "pe_mc": [_safe(v) for v in vni_sect.get("pe_mc", pd.Series([0]*len(vni_sect)))],
                "pe_ern": [_safe(v) for v in vni_sect.get("pe_ern", pd.Series([0]*len(vni_sect)))],
                "pb_mc": [_safe(v) for v in vni_sect.get("pb_mc", pd.Series([0]*len(vni_sect)))],
                "pb_bv": [_safe(v) for v in vni_sect.get("pb_bv", pd.Series([0]*len(vni_sect)))],
                "is_index": True,
            }
        }
    else:
        vni_sub = (
            tick_5y.groupby("date")[["pe", "pb"]]
            .median()
            .reset_index()
            .sort_values("date")
        ).merge(vni_sums, on="date", how="left")
        trend = {
            "VN-Index": {
                "dates": vni_sub["date"].dt.strftime("%Y-%m-%d").tolist(),
                "pe":    [_safe(v) for v in vni_sub["pe"]],
                "pb":    [_safe(v) for v in vni_sub["pb"]],
                "w_pe":  [],
                "w_pb":  [],
                "pe_mc": [_safe(v) for v in vni_sub.get("pe_mc", pd.Series([0]*len(vni_sub)))],
                "pe_ern": [_safe(v) for v in vni_sub.get("pe_ern", pd.Series([0]*len(vni_sub)))],
                "pb_mc": [_safe(v) for v in vni_sub.get("pb_mc", pd.Series([0]*len(vni_sub)))],
                "pb_bv": [_safe(v) for v in vni_sub.get("pb_bv", pd.Series([0]*len(vni_sub)))],
                "is_index": True,
            }
        }

    for grp in all_groups:
        sub = sect_5y[sect_5y["group"] == grp].sort_values("date")
        sub = sub.merge(grp_sums[grp_sums["group"] == grp], on="date", how="left")
        trend[grp] = {
            "dates": sub["date"].dt.strftime("%Y-%m-%d").tolist(),
            "pe":    [_safe(v) for v in sub["median_pe"]],
            "pb":    [_safe(v) for v in sub["median_pb"]],
            "w_pe":  [_safe(v) for v in sub.get("weighted_pe", pd.Series([np.nan]*len(sub)))],
            "w_pb":  [_safe(v) for v in sub.get("weighted_pb", pd.Series([np.nan]*len(sub)))],
            "pe_mc": [_safe(v) for v in sub.get("pe_mc", pd.Series([0]*len(sub)))],
            "pe_ern": [_safe(v) for v in sub.get("pe_ern", pd.Series([0]*len(sub)))],
            "pb_mc": [_safe(v) for v in sub.get("pb_mc", pd.Series([0]*len(sub)))],
            "pb_bv": [_safe(v) for v in sub.get("pb_bv", pd.Series([0]*len(sub)))],
            "is_index": False,
        }

    tbl_cols = ["ticker","close","pe","pb","eps_ttm","bvps","shares","sector","industry","group"]
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
<title>📊  VN-HOSE P/E & P/B 🔍</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js"></script>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css"/>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>

<style>
/* ── CSS custom properties: all colours go here ─────────────────────── */
:root {
  --bg:             #0f172a;
  --card:           #1e293b;
  --card2:          #0f172a;
  --border:         #334155;
  --text:           #e2e8f0;
  --muted:          #94a3b8;
  --dim:            #64748b;
  --accent:         #38bdf8;
  --accent2:        #818cf8;
  --green:          #4ade80;
  --yellow:         #facc15;
  --orange:         #fb923c;
  --red:            #f87171;
  --grid:           #1e3a5f;
  --hover:          #1e3a5f;
  --vg-bg:          #0f172a;
  --vg-border:      #1d4ed8;
  --btn-bg:         #1e293b;
  --btn-border:     #475569;
  --shadow:         rgba(0,0,0,0.4);
  --pe-card-bg:     rgba(250, 204, 21, 0.08);
  --pe-card-border: rgba(250, 204, 21, 0.35);
  --pb-card-bg:     rgba(244, 114, 182, 0.08);
  --pb-card-border: rgba(244, 114, 182, 0.35);
}
[data-theme="light"] {
  --bg:             #f1f5f9;
  --card:           #ffffff;
  --card2:          #f8fafc;
  --border:         #e2e8f0;
  --text:           #1e293b;
  --muted:          #475569;
  --dim:            #94a3b8;
  --accent:         #0284c7;
  --accent2:        #6366f1;
  --green:          #16a34a;
  --yellow:         #b45309;
  --orange:         #c2410c;
  --red:            #dc2626;
  --grid:           #e2e8f0;
  --hover:          #eff6ff;
  --vg-bg:          #eff6ff;
  --vg-border:      #3b82f6;
  --btn-bg:         #ffffff;
  --btn-border:     #cbd5e1;
  --shadow:         rgba(0,0,0,0.08);
  --pe-card-bg:     #fef9c3;
  --pe-card-border: #fde047;
  --pb-card-bg:     #fce7f3;
  --pb-card-border: #f9a8d4;
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
.grid-6 { display: grid; grid-template-columns: repeat(2,1fr); gap: 14px; }
.grid-2 { display: grid; grid-template-columns: repeat(1,1fr); gap: 16px; }
@media(min-width:640px)  { 
  .grid-4 { grid-template-columns: repeat(4,1fr); }
  .grid-6 { grid-template-columns: repeat(3,1fr); }
}
@media(min-width:1024px) { 
  .grid-2 { grid-template-columns: repeat(2,1fr); }
  .grid-6 { grid-template-columns: repeat(6,1fr); }
}
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
.card-pe {
  background: var(--pe-card-bg) !important;
  border: 1.5px solid var(--pe-card-border) !important;
}
.card-pb {
  background: var(--pb-card-bg) !important;
  border: 1.5px solid var(--pb-card-border) !important;
}
.card-hose.card-pe, .card-hose.card-pb {
  border-width: 2px !important;
}

/* ── Top HOSE Hero Cards (Standing out prominently) ──────────────────── */
.card-hose {
  background: linear-gradient(135deg, var(--card) 0%, var(--card2) 100%);
  border: 2px solid var(--accent) !important;
  border-radius: 16px;
  padding: 22px 20px;
  box-shadow: 0 6px 20px var(--shadow);
  position: relative;
  overflow: hidden;
  transition: transform .2s, box-shadow .2s;
}
.card-hose:hover {
  transform: translateY(-3px);
  box-shadow: 0 10px 25px var(--shadow);
}
.card-hose::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 4px;
  background: linear-gradient(90deg, var(--accent), var(--accent2));
}
.card-hose .lbl {
  color: var(--accent);
  font-size: .75rem;
  font-weight: 800;
  letter-spacing: .08em;
}
.card-hose .big {
  font-size: 2.3rem;
  font-weight: 900;
  margin: 6px 0 2px;
}

/* ── Status Banner ────────────────────────────────────────────────────── */
.status-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  background: linear-gradient(135deg, rgba(56, 189, 248, 0.1) 0%, rgba(129, 140, 248, 0.1) 100%);
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 12px;
  padding: 12px 18px;
  font-size: 0.88rem;
  color: var(--text);
  box-shadow: 0 4px 12px var(--shadow);
  backdrop-filter: blur(8px);
}
.status-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.status-icon {
  font-size: 1.1rem;
}
.status-label {
  color: var(--muted);
}
.status-val {
  color: var(--accent);
  font-weight: 700;
}
.status-divider {
  width: 1px;
  height: 20px;
  background: var(--border);
}
@media (max-width: 640px) {
  .status-divider { display: none; }
  .status-banner { flex-direction: column; align-items: flex-start; gap: 8px; }
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
/* ── Trend Controls ───────────────────────────────────────────────────── */
.trend-btn {
  background: var(--btn-bg);
  border: 1px solid var(--btn-border);
  color: var(--text);
  padding: 4px 10px;
  border-radius: 6px;
  font-size: .75rem;
  font-weight: 600;
  cursor: pointer;
  transition: all .2s;
}
.trend-btn:hover, .trend-btn.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}
.trend-input {
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 3px 8px;
  border-radius: 6px;
  font-size: .75rem;
}
.sector-chip {
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--dim);
  padding: 4px 12px;
  border-radius: 14px;
  font-size: .75rem;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 5px;
  transition: all .2s;
  white-space: nowrap;
}
.sector-chip:hover { border-color: var(--accent); color: var(--text); }
.sector-chip.action {
  background: var(--card2);
  color: var(--muted);
  border-style: dashed;
}
.sector-chip.action:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.chip-index {
  font-weight: 700;
  letter-spacing: .02em;
}
.chip-divider {
  border-left: 1px solid var(--border);
  height: 22px;
  margin: 0 4px;
  align-self: center;
}
.trend-section-label {
  font-size: .7rem;
  font-weight: 700;
  color: var(--dim);
  text-transform: uppercase;
  letter-spacing: .06em;
  padding: 4px 0;
  align-self: center;
}

/* ── Fullscreen Chart Mode ─────────────────────────────────────────────────── */
.card:fullscreen,
.card.is-fullscreen {
  position: fixed !important;
  top: 0 !important;
  left: 0 !important;
  width: 100vw !important;
  height: 100vh !important;
  z-index: 99999 !important;
  background: var(--card) !important;
  border-radius: 0 !important;
  margin: 0 !important;
  padding: 24px 32px !important;
  display: flex !important;
  flex-direction: column !important;
  overflow-y: auto !important;
  box-sizing: border-box !important;
}
.card:fullscreen .chart-canvas-box,
.card.is-fullscreen .chart-canvas-box {
  flex: 1 1 auto !important;
  min-height: 65vh !important;
  max-height: none !important;
  width: 100% !important;
  position: relative !important;
  display: flex !important;
  flex-direction: column !important;
}
.card:fullscreen .chart-canvas-box canvas,
.card.is-fullscreen .chart-canvas-box canvas {
  max-height: none !important;
  flex: 1 1 auto !important;
  width: 100% !important;
  height: 100% !important;
}
</style>
</head>

<body>
<div class="page">

  <!-- ── Status Notification Banner ──────────────────────────────────────── -->
  <div class="status-banner mb-6">
    <div class="status-item">
      <span class="status-icon">🔄</span>
      <span class="status-label">Dữ liệu cập nhật:</span>
      <strong class="status-val" id="banner-updated">__UPDATED_AT__</strong>
    </div>
    <div class="status-divider"></div>
    <div class="status-item">
      <span class="status-icon">🕒</span>
      <span class="status-label">Truy cập lúc:</span>
      <strong class="status-val" id="banner-accessed">Đang tải...</strong>
    </div>
  </div>

  <!-- ── Header ──────────────────────────────────────────────────────────── -->
  <header class="hdr">
    <div class="hdr-left">
      <h1>📊  VN-HOSE P/E &amp; P/B 🔍</h1>
    </div>
    <button class="theme-btn" id="theme-btn" onclick="toggleTheme()">
      <span id="theme-icon">☀️</span>
      <span id="theme-label">Light mode</span>
    </button>
  </header>

  <!-- ── Market summary ─────────────────────────────────────────────────── -->
  <div class="grid-6 mb-8">
    <div class="card card-hose card-pe">
      <div class="lbl">HOSE Median P/E</div>
      <div class="big" id="mkt-pe">—</div>
      <div class="sub">Unweighted Median</div>
    </div>
    <div class="card card-hose card-pe">
      <div class="lbl">HOSE Weighted P/E</div>
      <div class="big" style="color:#38bdf8" id="mkt-wpe">—</div>
      <div class="sub">Market-Cap Weighted</div>
    </div>
    <div class="card card-hose card-pb">
      <div class="lbl">HOSE Median P/B</div>
      <div class="big" style="color:var(--accent2)" id="mkt-pb">—</div>
      <div class="sub">Unweighted Median</div>
    </div>
    <div class="card card-hose card-pb">
      <div class="lbl">HOSE Weighted P/B</div>
      <div class="big" style="color:#f472b6" id="mkt-wpb">—</div>
      <div class="sub">Market-Cap Weighted</div>
    </div>
    <div class="card card-hose card-pe">
      <div class="lbl">Stocks with P/E</div>
      <div class="big" style="color:var(--text)" id="mkt-npe">—</div>
      <div class="sub" id="mkt-total"></div>
    </div>
    <div class="card card-hose card-pb">
      <div class="lbl">Stocks with P/B</div>
      <div class="big" style="color:var(--text)" id="mkt-npb">—</div>
      <div class="sub">Valid values</div>
    </div>
  </div>

  <!-- ── Custom VN-Index Calculator Tool ──────────────────────────────── -->
  <div class="card mb-8">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:12px">
      <div>
        <div class="sec-title">🧮 VN-Index Custom Calculator (Loại trừ Nhóm ngành tùy chọn)</div>
        <div class="sec-sub" style="margin-bottom:0">Nhấn chọn thủ công các nhóm ngành muốn loại trừ để xem định giá thực tế của phần thị trường còn lại</div>
      </div>
      <button class="trend-btn" onclick="resetExclusions()" style="padding:6px 12px">↺ Đặt lại mặc định</button>
    </div>

    <!-- Exclusion Checkbox Chips -->
    <div style="background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:16px">
      <div style="font-size:.75rem;font-weight:700;color:var(--muted);margin-bottom:8px">❌ Nhấn để chọn nhóm ngành muốn LOẠI TRỪ (Exclude):</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center" id="exclusion-chips"></div>
    </div>

    <!-- Real-time Computed Stats Grid -->
    <div class="grid-6" style="gap:12px">
      <div class="card-pe" style="border-radius:10px;padding:12px">
        <div class="lbl">Custom Median P/E</div>
        <div style="display:flex;align-items:baseline;gap:8px">
          <span class="big" id="custom-pe">—</span>
          <span id="diff-pe" style="font-size:.85rem;font-weight:700"></span>
        </div>
        <div class="sub" id="custom-pe-sub">Trung vị (Unweighted)</div>
      </div>
      <div class="card-pe" style="border-radius:10px;padding:12px">
        <div class="lbl">Custom Weighted P/E</div>
        <div style="display:flex;align-items:baseline;gap:8px">
          <span class="big" style="color:#38bdf8" id="custom-wpe">—</span>
          <span id="diff-wpe" style="font-size:.85rem;font-weight:700"></span>
        </div>
        <div class="sub" id="custom-wpe-sub">Trọng số Vốn hóa (Weighted)</div>
      </div>
      <div class="card-pb" style="border-radius:10px;padding:12px">
        <div class="lbl">Custom Median P/B</div>
        <div style="display:flex;align-items:baseline;gap:8px">
          <span class="big" style="color:var(--accent2)" id="custom-pb">—</span>
          <span id="diff-pb" style="font-size:.85rem;font-weight:700"></span>
        </div>
        <div class="sub" id="custom-pb-sub">Trung vị (Unweighted)</div>
      </div>
      <div class="card-pb" style="border-radius:10px;padding:12px">
        <div class="lbl">Custom Weighted P/B</div>
        <div style="display:flex;align-items:baseline;gap:8px">
          <span class="big" style="color:#f472b6" id="custom-wpb">—</span>
          <span id="diff-wpb" style="font-size:.85rem;font-weight:700"></span>
        </div>
        <div class="sub" id="custom-wpb-sub">Trọng số Vốn hóa (Weighted)</div>
      </div>
      <div style="background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:12px">
        <div class="lbl">Số cổ phiếu hợp lệ còn lại</div>
        <div class="big" style="color:var(--green);font-size:1.6rem" id="custom-count">—</div>
        <div class="sub" id="custom-excluded-info">Chưa loại trừ nhóm nào</div>
      </div>
      <div style="background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:12px">
        <div class="lbl">Custom Mean (TB cộng)</div>
        <div style="font-size:.95rem;font-weight:700;color:var(--text);margin-top:6px;background:var(--pe-card-bg);padding:2px 6px;border-radius:4px" id="custom-mean-pe">Mean P/E: —</div>
        <div style="font-size:.95rem;font-weight:700;color:var(--text);margin-top:6px;background:var(--pb-card-bg);padding:2px 6px;border-radius:4px" id="custom-mean-pb">Mean P/B: —</div>
      </div>
    </div>
  </div>

  <!-- ── Custom VN-Index 5-Year Trend ────────────────────────────────────── -->
  <div class="card mb-8" id="card-trend-custom">
    <div class="sec-title">📈 Custom VN-Index 5-Year Interactive Trend (So sánh định giá với VN-Index Gốc)</div>
    <div class="sec-sub">Đường liền màu sáng là VN-Index sau khi loại trừ · Đường đứt nét là VN-Index gốc toàn thị trường · Điều chỉnh thời gian tùy ý trong vòng 5 năm</div>

    <!-- Controls Bar -->
    <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;background:var(--card2);padding:10px 14px;border-radius:8px;border:1px solid var(--border);margin-bottom:14px">
      <!-- Metric -->
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span style="font-size:.75rem;font-weight:700;color:var(--muted)">Metric:</span>
        <button class="trend-btn active" id="cust-btn-wpe" onclick="setCustomMetric('wpe')">Weighted P/E</button>
        <button class="trend-btn" id="cust-btn-wpb" onclick="setCustomMetric('wpb')">Weighted P/B</button>
      </div>
      <!-- Period presets -->
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span style="font-size:.75rem;font-weight:700;color:var(--muted)">Giai đoạn:</span>
        <button class="trend-btn active" id="cust-range-5Y" onclick="setCustomRange('5Y')">5 Năm (Tất cả)</button>
        <button class="trend-btn" id="cust-range-3Y" onclick="setCustomRange('3Y')">3 Năm</button>
        <button class="trend-btn" id="cust-range-1Y" onclick="setCustomRange('1Y')">1 Năm</button>
        <button class="trend-btn" id="cust-range-YTD" onclick="setCustomRange('YTD')">YTD</button>
      </div>
      <!-- Custom date range -->
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span style="font-size:.75rem;color:var(--muted)">Từ:</span>
        <input type="date" id="cust-date-from" class="trend-input" onchange="applyCustomDateRange()"/>
        <span style="font-size:.75rem;color:var(--muted)">Đến:</span>
        <input type="date" id="cust-date-to" class="trend-input" onchange="applyCustomDateRange()"/>
        <button class="trend-btn" onclick="resetCustomZoom()">↺ Reset</button>
        <button class="trend-btn fullscreen-btn" style="font-weight:700;color:var(--accent)" onclick="toggleFullscreen('card-trend-custom', this)">⛶ Toàn màn hình</button>
      </div>
    </div>

    <div class="chart-canvas-box" style="position:relative; width:100%">
      <canvas id="chart-custom-trend" style="max-height:400px"></canvas>
    </div>
    <p id="cust-trend-msg" style="color:var(--dim);font-size:.72rem;margin-top:8px">
      💡 Kéo ngang để zoom · Cuộn chuột / pinch để điều chỉnh thời gian xem
    </p>
  </div>

  <!-- ── Sector charts ──────────────────────────────────────────────────── -->
  <div class="grid-2 mb-8">
    <div class="card card-pe">
      <div class="sec-title">📊 Sector Median P/E</div>
      <div class="sec-sub">Colour: green &lt;12 · blue &lt;20 · yellow &lt;30 · red ≥30</div>
      <div style="position:relative; height:380px; width:100%">
        <canvas id="chart-pe"></canvas>
      </div>
    </div>
    <div class="card card-pb">
      <div class="sec-title">📊 Sector Median P/B</div>
      <div class="sec-sub">Lower = cheaper relative to book value</div>
      <div style="position:relative; height:380px; width:100%">
        <canvas id="chart-pb"></canvas>
      </div>
    </div>
  </div>
  <div class="grid-2 mb-8">
    <div class="card card-pe">
      <div class="sec-title">📊 Sector Weighted P/E</div>
      <div class="sec-sub">Trọng số vốn hóa · Colour: green &lt;12 · blue &lt;20 · yellow &lt;30 · red ≥30</div>
      <div style="position:relative; height:380px; width:100%">
        <canvas id="chart-wpe"></canvas>
      </div>
    </div>
    <div class="card card-pb">
      <div class="sec-title">📊 Sector Weighted P/B</div>
      <div class="sec-sub">Trọng số vốn hóa · Lower = cheaper relative to book value</div>
      <div style="position:relative; height:380px; width:100%">
        <canvas id="chart-wpb"></canvas>
      </div>
    </div>
  </div>

  <!-- ── 5-Year Trend ───────────────────────────────────────────────────── -->
  <div class="card mb-8" id="card-trend-main">
    <div class="sec-title">📈 VN-Index &amp; Sector 5-Year P/E · P/B Interactive Trend</div>
    <div class="sec-sub">Chọn tự do VN-Index và/hoặc các nhóm ngành · Điều chỉnh thời gian tùy ý trong vòng 5 năm</div>

    <!-- Selector Chips Row -->
    <div style="background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:12px">
      <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center" id="sector-chips"></div>
    </div>

    <!-- Controls Bar -->
    <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;background:var(--card2);padding:10px 14px;border-radius:8px;border:1px solid var(--border);margin-bottom:14px">
      <!-- Metric -->
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span style="font-size:.75rem;font-weight:700;color:var(--muted)">Metric:</span>
        <button class="trend-btn active" id="btn-metric-wpe" onclick="setMetric('wpe')">Weighted P/E</button>
        <button class="trend-btn" id="btn-metric-wpb" onclick="setMetric('wpb')">Weighted P/B</button>
        <button class="trend-btn" id="btn-metric-pe" onclick="setMetric('pe')">Median P/E</button>
        <button class="trend-btn" id="btn-metric-pb" onclick="setMetric('pb')">Median P/B</button>
        <button class="trend-btn" id="btn-metric-both" onclick="setMetric('both')">Cả hai Median</button>
      </div>
      <!-- Period presets -->
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span style="font-size:.75rem;font-weight:700;color:var(--muted)">Giai đoạn:</span>
        <button class="trend-btn active" id="range-5Y" onclick="setRange('5Y')">5 Năm (Tất cả)</button>
        <button class="trend-btn" id="range-3Y" onclick="setRange('3Y')">3 Năm</button>
        <button class="trend-btn" id="range-1Y" onclick="setRange('1Y')">1 Năm</button>
        <button class="trend-btn" id="range-YTD" onclick="setRange('YTD')">YTD</button>
      </div>
      <!-- Custom date range -->
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span style="font-size:.75rem;color:var(--muted)">Từ:</span>
        <input type="date" id="date-from" class="trend-input" onchange="applyCustomRange()"/>
        <span style="font-size:.75rem;color:var(--muted)">Đến:</span>
        <input type="date" id="date-to" class="trend-input" onchange="applyCustomRange()"/>
        <button class="trend-btn" onclick="resetZoom()">↺ Reset</button>
        <button class="trend-btn fullscreen-btn" style="font-weight:700;color:var(--accent)" onclick="toggleFullscreen('card-trend-main', this)">⛶ Toàn màn hình</button>
      </div>
    </div>

    <div class="chart-canvas-box" style="position:relative; width:100%">
      <canvas id="chart-trend" style="max-height:400px"></canvas>
    </div>
    <p id="trend-msg" style="color:var(--dim);font-size:.72rem;margin-top:8px">
      💡 Kéo ngang để zoom · Cuộn chuột / pinch để điều chỉnh thời gian xem
    </p>
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
    © Bản quyền thuộc về FTU-kudo
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
    grid:    dk ? '#1e3a5f' : '#e2e8f0',
    ticks:   dk ? '#94a3b8' : '#475569',
    legend:  dk ? '#94a3b8' : '#475569',
    refLine: dk ? '#ffffff' : '#111827',
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
    if (ch === charts.customTrend && ch.data.datasets?.[1]) {
      ch.data.datasets[1].borderColor = c.refLine;
    }
    ch.update('none');
  });
}

window.toggleFullscreen = function(cardId, btnEl) {
  const card = document.getElementById(cardId);
  if (!card) return;

  const isFull = document.fullscreenElement === card || card.classList.contains('is-fullscreen');
  if (isFull) {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(()=>{});
    }
    card.classList.remove('is-fullscreen');
    if (btnEl) btnEl.innerHTML = '⛶ Toàn màn hình';
  } else {
    card.classList.add('is-fullscreen');
    if (card.requestFullscreen) {
      card.requestFullscreen().catch(() => {});
    }
    if (btnEl) btnEl.innerHTML = '❌ Thu nhỏ';
  }
  setTimeout(() => {
    Object.values(charts).forEach(ch => { if (ch && ch.resize) ch.resize(); });
  }, 150);
};

document.addEventListener('fullscreenchange', () => {
  ['card-trend-main', 'card-trend-custom'].forEach(id => {
    const card = document.getElementById(id);
    const btn = card?.querySelector('.fullscreen-btn');
    if (card && document.fullscreenElement !== card && !card.classList.contains('is-fullscreen')) {
      if (btn) btn.innerHTML = '⛶ Toàn màn hình';
    } else if (card && document.fullscreenElement !== card && card.classList.contains('is-fullscreen')) {
      card.classList.remove('is-fullscreen');
      if (btn) btn.innerHTML = '⛶ Toàn màn hình';
      setTimeout(() => {
        Object.values(charts).forEach(ch => { if (ch && ch.resize) ch.resize(); });
      }, 100);
    }
  });
});

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
const VNI_COLOR = '#ffd700'; // Gold for VN-Index

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

  // ── Status banner timestamps in UTC+7 (Vietnam time)
  const accessedEl = document.getElementById('banner-accessed');
  if (accessedEl) {
    const now = new Date();
    const timeFormatter = new Intl.DateTimeFormat('vi-VN', {
      timeZone: 'Asia/Ho_Chi_Minh',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    });
    const dateFormatter = new Intl.DateTimeFormat('vi-VN', {
      timeZone: 'Asia/Ho_Chi_Minh',
      day: '2-digit', month: '2-digit', year: 'numeric'
    });
    accessedEl.textContent = `${timeFormatter.format(now)}, Ngày ${dateFormatter.format(now)} (UTC+7)`;
  }

  // Sync theme toggle button text now that DOM exists
  const dk0 = INIT_THEME === 'dark';
  document.getElementById('theme-icon').textContent  = dk0 ? '☀️' : '🌙';
  document.getElementById('theme-label').textContent = dk0 ? 'Light mode' : 'Dark mode';

  // ── Header
  const m = D.market;
  const hdrDateEl = document.getElementById('hdr-date');
  if (hdrDateEl) hdrDateEl.textContent = m.date;
  document.getElementById('mkt-pe').textContent    = fmt(m.median_pe);
  document.getElementById('mkt-wpe').textContent   = fmt(m.weighted_pe);
  document.getElementById('mkt-pb').textContent    = fmt(m.median_pb);
  document.getElementById('mkt-wpb').textContent   = fmt(m.weighted_pb);
  document.getElementById('mkt-npe').textContent   = m.valid_pe;
  document.getElementById('mkt-npb').textContent   = m.valid_pb;
  document.getElementById('mkt-total').textContent = `of ${m.total} listed`;

  // ── Vingroup cards (if present)
  const vgEl = document.getElementById('vg-cards');
  if (vgEl && D.vingroup) {
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
  }

  // ── Sector P/E chart
  const tc = themeColors(INIT_THEME);
  const sects  = D.sectors.filter(s => s.median_pe != null);
  const sLabels = sects.map(s => s.group);
  const sPe     = sects.map(s => s.median_pe);

  const barBase = (axis) => ({
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: tc.grid }, ticks: { color: tc.ticks } },
      y: { grid: { color: tc.grid }, ticks: { color: tc.ticks, font: { size: 11 }, autoSkip: false } },
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

  // ── Sector Weighted P/E chart
  const sWPe = D.sectors.filter(s => s.weighted_pe != null).slice().sort((a,b) => a.weighted_pe - b.weighted_pe);
  charts.wpe = new Chart(document.getElementById('chart-wpe'), {
    type: 'bar',
    data: {
      labels: sWPe.map(s => s.group),
      datasets: [{ data: sWPe.map(s => s.weighted_pe), backgroundColor: sWPe.map(s => peCol(s.weighted_pe)), borderRadius: 5 }],
    },
    options: {
      ...barBase(),
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` Weighted P/E: ${ctx.parsed.x.toFixed(1)}` } },
      },
    },
  });

  // ── Sector Median P/B chart (matching exact y-axis sector order of Sector Median P/E)
  charts.pb  = new Chart(document.getElementById('chart-pb'), {
    type: 'bar',
    data: {
      labels: sLabels,
      datasets: [{ data: sects.map(s => s.median_pb), backgroundColor: 'var(--accent2)', borderRadius: 5 }],
    },
    options: {
      ...barBase(),
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` P/B: ${ctx.parsed.x.toFixed(2)}` } },
      },
    },
  });

  // ── Sector Weighted P/B chart (matching exact y-axis sector order of Sector Weighted P/E)
  charts.wpb = new Chart(document.getElementById('chart-wpb'), {
    type: 'bar',
    data: {
      labels: sWPe.map(s => s.group),
      datasets: [{ data: sWPe.map(s => s.weighted_pb), backgroundColor: '#f472b6', borderRadius: 5 }],
    },
    options: {
      ...barBase(),
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` Weighted P/B: ${ctx.parsed.x.toFixed(2)}` } },
      },
    },
  });

  // ── 5-Year Interactive Trend (VN-Index + All Sectors)
  const tGroups = Object.keys(D.trend); // VN-Index is first
  // Sector-only groups (exclude VN-Index for separate chip category)
  const sectorGroups = tGroups.filter(g => !D.trend[g].is_index);
  const indexGroups  = tGroups.filter(g => D.trend[g].is_index);

  let currentMetric = 'wpe';
  let selectedGroups = new Set(tGroups); // all selected by default
  // Track active range button
  let activeRange = '5Y';

  window.setMetric = function(m) {
    currentMetric = m;
    ['wpe', 'wpb', 'pe', 'pb', 'both'].forEach(k => {
      const btn = document.getElementById('btn-metric-' + k);
      if (btn) btn.className = 'trend-btn' + (k === m ? ' active' : '');
    });
    renderTrendChart();
  };

  window.toggleGroup = function(grp) {
    if (selectedGroups.has(grp)) {
      selectedGroups.delete(grp);
    } else {
      selectedGroups.add(grp);
    }
    renderChips();
    renderTrendChart();
  };

  window.selectAll = function(select) {
    if (select) {
      tGroups.forEach(g => selectedGroups.add(g));
    } else {
      selectedGroups.clear();
    }
    renderChips();
    renderTrendChart();
  };

  window.selectOnlyIndex = function() {
    selectedGroups.clear();
    indexGroups.forEach(g => selectedGroups.add(g));
    renderChips();
    renderTrendChart();
  };

  window.selectTop5 = function() {
    selectedGroups.clear();
    indexGroups.forEach(g => selectedGroups.add(g)); // always include VN-Index
    sectorGroups.slice(0, 5).forEach(g => selectedGroups.add(g));
    renderChips();
    renderTrendChart();
  };

  function chipColor(grp, idx) {
    if (D.trend[grp] && D.trend[grp].is_index) return VNI_COLOR;
    return PALETTE[idx % PALETTE.length];
  }

  function renderChips() {
    const container = document.getElementById('sector-chips');
    if (!container) return;
    let html = '';

    // Action buttons
    html += `<button class="sector-chip action" onclick="selectAll(true)">✓ Chọn tất cả</button>`;
    html += `<button class="sector-chip action" onclick="selectAll(false)">✕ Bỏ chọn</button>`;
    html += `<button class="sector-chip action" onclick="selectOnlyIndex()">📊 Chỉ VN-Index</button>`;
    html += `<button class="sector-chip action" onclick="selectTop5()">★ VN-Index + Top 5</button>`;
    html += `<span class="chip-divider"></span>`;

    // VN-Index chips (special gold)
    indexGroups.forEach(grp => {
      const active = selectedGroups.has(grp);
      const style = active
        ? `border-color:${VNI_COLOR};background:${VNI_COLOR}33;color:#fff`
        : `border-color:var(--border);color:var(--dim)`;
      const esc = grp.replace(/'/g, "\\'");
      html += `<button class="sector-chip chip-index" style="${style}" onclick="toggleGroup('${esc}')">`;
      html += `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${VNI_COLOR};flex-shrink:0"></span>`;
      html += `${grp}</button>`;
    });

    html += `<span class="chip-divider"></span>`;
    html += `<span class="trend-section-label">Ngành:</span>`;

    // Sector chips
    sectorGroups.forEach((grp, i) => {
      const color = PALETTE[i % PALETTE.length];
      const active = selectedGroups.has(grp);
      const style = active
        ? `border-color:${color};background:${color}22;color:var(--text)`
        : `border-color:var(--border);color:var(--dim)`;
      const esc = grp.replace(/'/g, "\\'");
      html += `<button class="sector-chip" style="${style}" onclick="toggleGroup('${esc}')">`;
      html += `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0"></span>`;
      html += `${grp}</button>`;
    });

    container.innerHTML = html;
  }

  function getDatasets() {
    const ds = [];
    let sectorIdx = 0;
    tGroups.forEach(grp => {
      if (!selectedGroups.has(grp)) {
        if (!D.trend[grp].is_index) sectorIdx++;
        return;
      }
      const isIndex = D.trend[grp].is_index;
      const color = isIndex ? VNI_COLOR : PALETTE[sectorIdx % PALETTE.length];
      const bw = isIndex ? 3 : 2;

      // Build date→value lookup for this group
      const peLookup = {}, pbLookup = {}, wpeLookup = {}, wpbLookup = {};
      D.trend[grp].dates.forEach((d, j) => {
        peLookup[d] = D.trend[grp].pe[j];
        pbLookup[d] = D.trend[grp].pb[j];
        wpeLookup[d] = (D.trend[grp].w_pe && D.trend[grp].w_pe[j] !== undefined) ? D.trend[grp].w_pe[j] : null;
        wpbLookup[d] = (D.trend[grp].w_pb && D.trend[grp].w_pb[j] !== undefined) ? D.trend[grp].w_pb[j] : null;
      });
      // Align to ALL_DATES: null for any date this group has no data
      const peAligned = ALL_DATES.map(d => peLookup.hasOwnProperty(d) ? peLookup[d] : null);
      const pbAligned = ALL_DATES.map(d => pbLookup.hasOwnProperty(d) ? pbLookup[d] : null);
      const wpeAligned = ALL_DATES.map(d => wpeLookup.hasOwnProperty(d) ? wpeLookup[d] : null);
      const wpbAligned = ALL_DATES.map(d => wpbLookup.hasOwnProperty(d) ? wpbLookup[d] : null);

      if (currentMetric === 'pe' || currentMetric === 'both') {
        ds.push({
          label: `${grp} (Median P/E)`,
          data: peAligned,
          borderColor: color,
          backgroundColor: isIndex ? `${color}18` : 'transparent',
          fill: isIndex,
          tension: 0.2,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: bw,
          spanGaps: false,   // do NOT connect across missing (null) dates → no spikes
          yAxisID: 'y',
          order: isIndex ? 0 : 1,
        });
      }
      if (currentMetric === 'wpe') {
        ds.push({
          label: `${grp} (Weighted P/E)`,
          data: wpeAligned,
          borderColor: color,
          backgroundColor: isIndex ? `${color}18` : 'transparent',
          fill: isIndex,
          tension: 0.2,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: bw,
          spanGaps: false,
          yAxisID: 'y',
          order: isIndex ? 0 : 1,
        });
      }
      if (currentMetric === 'pb' || currentMetric === 'both') {
        ds.push({
          label: `${grp} (Median P/B)`,
          data: pbAligned,
          borderColor: color,
          borderDash: currentMetric === 'both' ? [6, 3] : [],
          backgroundColor: 'transparent',
          fill: false,
          tension: 0.2,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: currentMetric === 'both' ? (isIndex ? 2 : 1.5) : bw,
          spanGaps: false,
          yAxisID: currentMetric === 'both' ? 'y2' : 'y',
          order: isIndex ? 0 : 1,
        });
      }
      if (currentMetric === 'wpb') {
        ds.push({
          label: `${grp} (Weighted P/B)`,
          data: wpbAligned,
          borderColor: color,
          backgroundColor: isIndex ? `${color}18` : 'transparent',
          fill: isIndex,
          tension: 0.2,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: bw,
          spanGaps: false,
          yAxisID: 'y',
          order: isIndex ? 0 : 1,
        });
      }
      if (!isIndex) sectorIdx++;
    });
    return ds;
  }

  // ── Build sorted union of all trading dates for the x-axis labels
  function buildAllDates() {
    const dateSet = new Set();
    tGroups.forEach(g => D.trend[g].dates.forEach(d => dateSet.add(d)));
    return Array.from(dateSet).sort();
  }
  const ALL_DATES = buildAllDates();

  // Prebuild group sum lookup by date for fast Custom Trend Chart recomputation
  const lookupSum = {};
  tGroups.forEach(grp => {
    lookupSum[grp] = {};
    if (D.trend[grp] && D.trend[grp].dates) {
      D.trend[grp].dates.forEach((d, j) => {
        lookupSum[grp][d] = {
          pe_mc:  D.trend[grp].pe_mc  ? D.trend[grp].pe_mc[j]  : null,
          pe_ern: D.trend[grp].pe_ern ? D.trend[grp].pe_ern[j] : null,
          pb_mc:  D.trend[grp].pb_mc  ? D.trend[grp].pb_mc[j]  : null,
          pb_bv:  D.trend[grp].pb_bv  ? D.trend[grp].pb_bv[j]  : null,
          w_pe:   D.trend[grp].w_pe   ? D.trend[grp].w_pe[j]   : null,
          w_pb:   D.trend[grp].w_pb   ? D.trend[grp].w_pb[j]   : null,
        };
      });
    }
  });

  // ── Snap a target date-string to the nearest actual date in ALL_DATES (>= target)
  function snapDate(targetStr) {
    for (let i = 0; i < ALL_DATES.length; i++) {
      if (ALL_DATES[i] >= targetStr) return ALL_DATES[i];
    }
    return ALL_DATES[ALL_DATES.length - 1];
  }

  function renderTrendChart() {
    if (!document.getElementById('chart-trend')) return;
    if (charts.trend) {
      charts.trend.data.labels   = ALL_DATES;
      charts.trend.data.datasets = getDatasets();
      if (currentMetric === 'both') {
        charts.trend.options.scales.y2 = {
          position: 'right',
          title: { display: true, text: 'Median P/B', color: tc.ticks },
          grid: { drawOnChartArea: false },
          ticks: { color: tc.ticks },
        };
        charts.trend.options.scales.y.title.text = 'Median P/E';
      } else {
        delete charts.trend.options.scales.y2;
        charts.trend.options.scales.y.title.text = currentMetric === 'wpe' ? 'Weighted P/E' : (currentMetric === 'wpb' ? 'Weighted P/B' : (currentMetric === 'pb' ? 'Median P/B' : 'Median P/E'));
      }
      charts.trend.update();
      return;
    }

    charts.trend = new Chart(document.getElementById('chart-trend'), {
      type: 'line',
      data: { labels: ALL_DATES, datasets: getDatasets() },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        // ← no 'parsing: false' — indexed arrays are passed directly so Chart.js can
        //   map them to ALL_DATES labels correctly via position index
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: {
            type: 'category',
            ticks: {
              color: tc.ticks,
              maxRotation: 30,
              autoSkip: true,
              maxTicksLimit: 18,
              font: { size: 11 },
            },
            grid: { color: tc.grid },
          },
          y: {
            position: 'left',
            title: { display: true, text: currentMetric === 'wpe' ? 'Weighted P/E' : (currentMetric === 'wpb' ? 'Weighted P/B' : (currentMetric === 'pb' ? 'Median P/B' : 'Median P/E')), color: tc.ticks },
            grid: { color: tc.grid },
            ticks: { color: tc.ticks },
            min: 0,
          },
        },
        plugins: {
          legend: {
            position: 'bottom',
            labels: { color: tc.legend, font: { size: 11 }, boxWidth: 16, padding: 12 },
          },
          tooltip: {
            callbacks: {
              title: ctx => ctx[0]?.label || '',
              label: ctx => {
                const v = ctx.parsed.y;
                return ` ${ctx.dataset.label}: ${v != null ? v.toFixed(2) : '—'}`;
              },
            },
          },
          zoom: {
            zoom: {
              wheel: { enabled: true },
              pinch: { enabled: true },
              drag: {
                enabled: true,
                backgroundColor: 'rgba(255,215,0,0.12)',
                borderColor: 'rgba(255,215,0,0.8)',
                borderWidth: 1,
              },
              mode: 'x',
            },
            pan: { enabled: true, mode: 'x' },
          },
        },
      },
    });
  }

  function setRangeBtn(id) {
    ['5Y','3Y','1Y','YTD'].forEach(r => {
      const b = document.getElementById('range-' + r);
      if (b) b.className = 'trend-btn' + (r === id ? ' active' : '');
    });
    activeRange = id;
  }

  window.resetZoom = function() {
    if (!charts.trend) return;
    if (charts.trend.resetZoom) charts.trend.resetZoom();
    charts.trend.options.scales.x.min = undefined;
    charts.trend.options.scales.x.max = undefined;
    charts.trend.update();
    const fromEl = document.getElementById('date-from');
    const toEl   = document.getElementById('date-to');
    if (fromEl) fromEl.value = '';
    if (toEl)   toEl.value   = '';
    setRangeBtn('5Y');
  };

  window.setRange = function(period) {
    if (!charts.trend) return;
    setRangeBtn(period);
    const now = new Date();
    let minDate = null;
    if (period === '3Y') {
      minDate = new Date(now.getFullYear() - 3, now.getMonth(), now.getDate()).toISOString().split('T')[0];
    } else if (period === '1Y') {
      minDate = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate()).toISOString().split('T')[0];
    } else if (period === 'YTD') {
      minDate = `${now.getFullYear()}-01-01`;
    }
    // Snap to the nearest actual trading date so the category axis always finds a match
    const snappedMin = minDate ? snapDate(minDate) : undefined;
    charts.trend.options.scales.x.min = snappedMin;
    charts.trend.options.scales.x.max = undefined;
    charts.trend.update();
    if (document.getElementById('date-from')) document.getElementById('date-from').value = snappedMin || '';
    if (document.getElementById('date-to'))   document.getElementById('date-to').value   = '';
  };

  window.applyCustomRange = function() {
    if (!charts.trend) return;
    const f = document.getElementById('date-from').value;
    const t = document.getElementById('date-to').value;
    // Snap both ends to actual trading dates
    charts.trend.options.scales.x.min = f ? snapDate(f) : undefined;
    charts.trend.options.scales.x.max = t ? snapDate(t) : undefined;
    charts.trend.update();
    setRangeBtn('custom');
  };

  // ── Custom VN-Index Calculator Tool Logic
  const allSectorGroups = D.sectors.map(s => s.group);
  let excludedGroups = new Set();

  window.toggleExclusion = function(grp) {
    if (excludedGroups.has(grp)) {
      excludedGroups.delete(grp);
    } else {
      excludedGroups.add(grp);
    }
    renderExclusionChips();
    updateCustomVNIndex();
  };

  window.resetExclusions = function() {
    excludedGroups.clear();
    renderExclusionChips();
    updateCustomVNIndex();
  };

  function renderExclusionChips() {
    const container = document.getElementById('exclusion-chips');
    if (!container) return;
    let html = '';
    allSectorGroups.forEach((grp, i) => {
      const active = excludedGroups.has(grp);
      const color = PALETTE[i % PALETTE.length];
      const style = active
        ? `border-color:var(--red);background:var(--red)22;color:#f87171;text-decoration:line-through`
        : `border-color:var(--border);color:var(--dim)`;
      const esc = grp.replace(/'/g, "\\'");
      html += `<button class="sector-chip" style="${style}" onclick="toggleExclusion('${esc}')">`;
      html += active ? `✕ ` : ``;
      html += `${grp}</button>`;
    });
    container.innerHTML = html;
  }

  function updateCustomVNIndex() {
    const valid = D.tickers.filter(t => !excludedGroups.has(t.group));
    const peVals = valid.map(t => t.pe).filter(v => v != null && !isNaN(v)).sort((a,b) => a - b);
    const pbVals = valid.map(t => t.pb).filter(v => v != null && !isNaN(v)).sort((a,b) => a - b);

    const calcMedian = arr => {
      if (!arr.length) return null;
      const mid = Math.floor(arr.length / 2);
      return arr.length % 2 !== 0 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
    };
    const calcMean = arr => {
      if (!arr.length) return null;
      return arr.reduce((a,b) => a + b, 0) / arr.length;
    };

    const medPe = calcMedian(peVals);
    const medPb = calcMedian(pbVals);
    const meanPe = calcMean(peVals);
    const meanPb = calcMean(pbVals);

    const origPe = D.market.median_pe;
    const origPb = D.market.median_pb;
    const origWPe = D.market.weighted_pe;
    const origWPb = D.market.weighted_pb;

    let sumPeMc = 0, sumPeErn = 0;
    let sumPbMc = 0, sumPbBv = 0;
    valid.forEach(t => {
      if (t.pe != null && !isNaN(t.pe) && t.shares > 0 && t.eps_ttm != null) {
        sumPeMc += (t.close * t.shares);
        sumPeErn += (t.eps_ttm * t.shares);
      }
      if (t.pb != null && !isNaN(t.pb) && t.shares > 0 && t.bvps != null) {
        sumPbMc += (t.close * t.shares);
        sumPbBv += (t.bvps * t.shares);
      }
    });
    const wPe = (sumPeErn > 0) ? (sumPeMc / sumPeErn) : null;
    const wPb = (sumPbBv > 0) ? (sumPbMc / sumPbBv) : null;

    const elPe = document.getElementById('custom-pe');
    const elPb = document.getElementById('custom-pb');
    const elWPe = document.getElementById('custom-wpe');
    const elWPb = document.getElementById('custom-wpb');
    const diffPe = document.getElementById('diff-pe');
    const diffPb = document.getElementById('diff-pb');
    const diffWPe = document.getElementById('diff-wpe');
    const diffWPb = document.getElementById('diff-wpb');
    const elCount = document.getElementById('custom-count');
    const elInfo = document.getElementById('custom-excluded-info');
    const elMeanPe = document.getElementById('custom-mean-pe');
    const elMeanPb = document.getElementById('custom-mean-pb');

    if (elPe) elPe.textContent = medPe != null ? fmt(medPe) : '—';
    if (elPb) elPb.textContent = medPb != null ? fmt(medPb) : '—';
    if (elWPe) elWPe.textContent = wPe != null ? fmt(wPe) : '—';
    if (elWPb) elWPb.textContent = wPb != null ? fmt(wPb) : '—';
    if (elMeanPe) elMeanPe.textContent = `Mean P/E: ${meanPe != null ? fmt(meanPe) : '—'}`;
    if (elMeanPb) elMeanPb.textContent = `Mean P/B: ${meanPb != null ? fmt(meanPb) : '—'}`;
    if (elCount) elCount.textContent = `${peVals.length} / ${D.tickers.length}`;

    if (excludedGroups.size === 0) {
      if (diffPe) { diffPe.textContent = ''; diffPe.style.color = ''; }
      if (diffPb) { diffPb.textContent = ''; diffPb.style.color = ''; }
      if (diffWPe) { diffWPe.textContent = ''; diffWPe.style.color = ''; }
      if (diffWPb) { diffWPb.textContent = ''; diffWPb.style.color = ''; }
      if (elInfo) elInfo.textContent = 'Chưa loại trừ nhóm nào (bằng VN-Index gốc)';
    } else {
      if (diffPe && medPe != null && origPe != null) {
        const d = medPe - origPe;
        const sign = d > 0 ? '+' : '';
        diffPe.textContent = `(${sign}${d.toFixed(2)})`;
        diffPe.style.color = d < 0 ? 'var(--green)' : (d > 0 ? 'var(--red)' : 'var(--muted)');
      }
      if (diffPb && medPb != null && origPb != null) {
        const d = medPb - origPb;
        const sign = d > 0 ? '+' : '';
        diffPb.textContent = `(${sign}${d.toFixed(2)})`;
        diffPb.style.color = d < 0 ? 'var(--green)' : (d > 0 ? 'var(--red)' : 'var(--muted)');
      }
      if (diffWPe && wPe != null && origWPe != null) {
        const d = wPe - origWPe;
        const sign = d > 0 ? '+' : '';
        diffWPe.textContent = `(${sign}${d.toFixed(2)})`;
        diffWPe.style.color = d < 0 ? 'var(--green)' : (d > 0 ? 'var(--red)' : 'var(--muted)');
      }
      if (diffWPb && wPb != null && origWPb != null) {
        const d = wPb - origWPb;
        const sign = d > 0 ? '+' : '';
        diffWPb.textContent = `(${sign}${d.toFixed(2)})`;
        diffWPb.style.color = d < 0 ? 'var(--green)' : (d > 0 ? 'var(--red)' : 'var(--muted)');
      }
      if (elInfo) elInfo.textContent = `Đã loại trừ ${excludedGroups.size} nhóm (${D.tickers.length - valid.length} mã)`;
    }
    updateCustomTrendChart();
  }

  let currentCustomMetric = 'wpe'; // 'wpe' or 'wpb'
  let activeCustomRange = '5Y';

  window.setCustomMetric = function(m) {
    currentCustomMetric = ['wpe','wpb'].includes(m) ? m : 'wpe';
    ['wpe','wpb'].forEach(id => {
      const btn = document.getElementById('cust-btn-' + id);
      if (btn) btn.className = 'trend-btn' + (id === m ? ' active' : '');
    });
    updateCustomTrendChart();
  };

  window.setCustomRange = function(period) {
    if (!charts.customTrend) return;
    activeCustomRange = period;
    ['5Y','3Y','1Y','YTD'].forEach(r => {
      const b = document.getElementById('cust-range-' + r);
      if (b) b.className = 'trend-btn' + (r === period ? ' active' : '');
    });
    const now = new Date();
    let minDate = null;
    if (period === '3Y') {
      minDate = new Date(now.getFullYear() - 3, now.getMonth(), now.getDate()).toISOString().split('T')[0];
    } else if (period === '1Y') {
      minDate = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate()).toISOString().split('T')[0];
    } else if (period === 'YTD') {
      minDate = `${now.getFullYear()}-01-01`;
    }
    const snappedMin = minDate ? snapDate(minDate) : undefined;
    charts.customTrend.options.scales.x.min = snappedMin;
    charts.customTrend.options.scales.x.max = undefined;
    charts.customTrend.update();
    if (document.getElementById('cust-date-from')) document.getElementById('cust-date-from').value = snappedMin || '';
    if (document.getElementById('cust-date-to'))   document.getElementById('cust-date-to').value   = '';
  };

  window.applyCustomDateRange = function() {
    if (!charts.customTrend) return;
    const f = document.getElementById('cust-date-from').value;
    const t = document.getElementById('cust-date-to').value;
    charts.customTrend.options.scales.x.min = f ? snapDate(f) : undefined;
    charts.customTrend.options.scales.x.max = t ? snapDate(t) : undefined;
    charts.customTrend.update();
    ['5Y','3Y','1Y','YTD'].forEach(r => {
      const b = document.getElementById('cust-range-' + r);
      if (b) b.className = 'trend-btn';
    });
  };

  window.resetCustomZoom = function() {
    if (!charts.customTrend) return;
    if (charts.customTrend.resetZoom) charts.customTrend.resetZoom();
    charts.customTrend.options.scales.x.min = undefined;
    charts.customTrend.options.scales.x.max = undefined;
    charts.customTrend.update();
    const fromEl = document.getElementById('cust-date-from');
    const toEl   = document.getElementById('cust-date-to');
    if (fromEl) fromEl.value = '';
    if (toEl)   toEl.value   = '';
    window.setCustomRange('5Y');
  };

  function updateCustomTrendChart() {
    if (!document.getElementById('chart-custom-trend') || typeof Chart === 'undefined') return;

    // Calculate daily custom values across ALL_DATES
    const customData = [];
    const origData = [];
    ALL_DATES.forEach((d, idx) => {
      let sumPeMc = 0, sumPeErn = 0, sumPbMc = 0, sumPbBv = 0;
      allSectorGroups.forEach(grp => {
        if (!excludedGroups.has(grp) && lookupSum[grp] && lookupSum[grp][d]) {
          const e = lookupSum[grp][d];
          if (e.pe_mc != null && !isNaN(e.pe_mc)) sumPeMc += e.pe_mc;
          if (e.pe_ern != null && !isNaN(e.pe_ern)) sumPeErn += e.pe_ern;
          if (e.pb_mc != null && !isNaN(e.pb_mc)) sumPbMc += e.pb_mc;
          if (e.pb_bv != null && !isNaN(e.pb_bv)) sumPbBv += e.pb_bv;
        }
      });
      if (currentCustomMetric === 'wpe') {
        const cVal = (sumPeErn > 0) ? (sumPeMc / sumPeErn) : null;
        customData.push(cVal != null ? parseFloat(cVal.toFixed(4)) : null);
        const oEntry = lookupSum['VN-Index'] && lookupSum['VN-Index'][d];
        const oVal = oEntry && (oEntry.pe_ern > 0) ? (oEntry.pe_mc / oEntry.pe_ern) : (D.trend['VN-Index']?.w_pe?.[idx] ?? null);
        origData.push(oVal != null ? parseFloat(oVal.toFixed(4)) : null);
      } else {
        const cVal = (sumPbBv > 0) ? (sumPbMc / sumPbBv) : null;
        customData.push(cVal != null ? parseFloat(cVal.toFixed(4)) : null);
        const oEntry = lookupSum['VN-Index'] && lookupSum['VN-Index'][d];
        const oVal = oEntry && (oEntry.pb_bv > 0) ? (oEntry.pb_mc / oEntry.pb_bv) : (D.trend['VN-Index']?.w_pb?.[idx] ?? null);
        origData.push(oVal != null ? parseFloat(oVal.toFixed(4)) : null);
      }
    });

    const isWPe = currentCustomMetric === 'wpe';
    const mainColor = isWPe ? '#38bdf8' : '#f472b6';
    const mainLabel = isWPe ? 'Custom Weighted P/E' : 'Custom Weighted P/B';
    const origLabel = isWPe ? 'VN-Index Gốc (Weighted P/E)' : 'VN-Index Gốc (Weighted P/B)';
    const currTheme = document.documentElement.getAttribute('data-theme') || INIT_THEME;
    const currentTc = themeColors(currTheme);

    if (charts.customTrend) {
      charts.customTrend.data.labels = ALL_DATES;
      charts.customTrend.data.datasets[0].data = customData;
      charts.customTrend.data.datasets[0].label = mainLabel;
      charts.customTrend.data.datasets[0].borderColor = mainColor;
      charts.customTrend.data.datasets[1].data = origData;
      charts.customTrend.data.datasets[1].label = origLabel;
      charts.customTrend.data.datasets[1].borderColor = currentTc.refLine;
      charts.customTrend.options.scales.y.title.text = mainLabel;
      charts.customTrend.update();
      return;
    }

    charts.customTrend = new Chart(document.getElementById('chart-custom-trend'), {
      type: 'line',
      data: {
        labels: ALL_DATES,
        datasets: [
          {
            label: mainLabel,
            data: customData,
            borderColor: mainColor,
            borderWidth: 2.5,
            tension: 0.15,
            pointRadius: 0,
            pointHoverRadius: 5,
            spanGaps: true,
          },
          {
            label: origLabel,
            data: origData,
            borderColor: currentTc.refLine,
            borderWidth: 2,
            borderDash: [6, 4],
            tension: 0.15,
            pointRadius: 0,
            pointHoverRadius: 4,
            spanGaps: true,
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: {
            type: 'category',
            ticks: { color: tc.ticks, maxRotation: 30, autoSkip: true, maxTicksLimit: 18, font: { size: 11 } },
            grid: { color: tc.grid },
          },
          y: {
            position: 'left',
            title: { display: true, text: mainLabel, color: tc.ticks },
            grid: { color: tc.grid },
            ticks: { color: tc.ticks },
            min: 0,
          },
        },
        plugins: {
          legend: { position: 'bottom', labels: { color: tc.legend, font: { size: 11 }, boxWidth: 16, padding: 12 } },
          tooltip: {
            callbacks: {
              title: ctx => ctx[0]?.label || '',
              label: ctx => {
                const v = ctx.parsed.y;
                return ` ${ctx.dataset.label}: ${v != null ? v.toFixed(2) : '—'}`;
              },
              afterBody: ctx => {
                if (ctx.length >= 2 && ctx[0].parsed.y != null && ctx[1].parsed.y != null) {
                  const diff = ctx[0].parsed.y - ctx[1].parsed.y;
                  const sign = diff > 0 ? '+' : '';
                  return [`\nChênh lệch so với VN-Index gốc: ${sign}${diff.toFixed(2)}`];
                }
                return [];
              }
            },
          },
          zoom: {
            zoom: {
              wheel: { enabled: true },
              pinch: { enabled: true },
              drag: { enabled: true, backgroundColor: 'rgba(56,189,248,0.12)', borderColor: 'rgba(56,189,248,0.8)', borderWidth: 1 },
              mode: 'x',
            },
            pan: { enabled: true, mode: 'x' },
          },
        },
      },
    });
  }


  if (tGroups.length > 0) {
    renderChips();
    renderTrendChart();
    renderExclusionChips();
    updateCustomVNIndex();
  } else {
    document.getElementById('trend-msg').textContent =
      'Dữ liệu lịch sử sẽ được tích lũy dần theo ngày.';
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    Path(DOCS_DIR).mkdir(parents=True, exist_ok=True)

    utc_plus_7 = timezone(timedelta(hours=7))
    now_vn = datetime.now(utc_plus_7)
    updated_str = now_vn.strftime("%H:%M:%S, Ngày %d/%m/%Y (UTC+7)")

    tick_l, tick_5y, sect_l, sect_5y, latest_date = load_data()
    payload = build_payload(tick_l, tick_5y, sect_l, sect_5y, latest_date)

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"JSON saved -> {JSON_FILE}")

    html = HTML.replace("__DATA_JSON__",
                        json.dumps(payload, ensure_ascii=False, default=str))
    html = html.replace("__UPDATED_AT__", updated_str)
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard built -> {DASHBOARD_FILE}")


if __name__ == "__main__":
    build()

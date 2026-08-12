"""Build self-contained GitHub Pages dashboard with light/dark mode."""
import json, sys
from datetime import timedelta, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import (
    VINGROUP_TICKERS, VINGROUP_GROUP,
    TICKER_HIST_FILE, SECTOR_HIST_FILE,
    DOCS_DIR
)

SECTOR_TRANSLATION = {
    'Bán buôn': 'Wholesale',
    'Tiện ích': 'Utilities',
    'Thiết bị điện': 'Electrical Equipment',
    'Dịch vụ lưu trú, ăn uống, giải trí': 'Accommodation & Entertainment',
    'Ngân hàng': 'Banking',
    'Chứng khoán': 'Securities',
    'Bất động sản': 'Real Estate',
    'SX Phụ trợ': 'Auxiliary Manufacturing',
    'Chăm sóc sức khỏe': 'Healthcare',
    'Nông - Lâm - Ngư': 'Agriculture, Forestry & Fishery',
    'Vật liệu xây dựng': 'Construction Materials',
    'Xây dựng': 'Construction',
    'Khai khoáng': 'Mining',
    'Bán lẻ': 'Retail',
    'Hóa chất': 'Chemicals',
    'SX Thiết bị, máy móc': 'Machinery & Equipment',
    'Thực phẩm - Đồ uống': 'Food & Beverage',
    'Vận tải - kho bãi': 'Transportation & Logistics',
    'Công nghệ và thông tin': 'Information Technology',
    'SX Hàng gia dụng': 'Household Goods',
    'Bảo hiểm': 'Insurance',
    'Chế biến Thủy sản': 'Seafood Processing',
    'Sản phẩm cao su': 'Rubber Products',
    'Dịch vụ tư vấn, hỗ trợ': 'Consulting & Support Services',
    'Tài chính khác': 'Other Financials',
    'Unknown': 'Unknown',
    'Vingroup Ecosystem': 'Vingroup Ecosystem',
    'VN-Index': 'VN-Index'
}

UI_STR = {
    'en': {
        'page_title': 'VN-HOSE P/E & P/B Dashboard',
        'title': '📊 VN-HOSE P/E & P/B',
        'as_of': 'As of',
        'valid_stocks': 'Valid Stocks',
        'out_of': 'out of',
        'light_mode': 'Light mode',
        'dark_mode': 'Dark mode',
        'hose_wpe': 'HOSE Weighted P/E',
        'hose_pe': 'HOSE Median P/E',
        'hose_wpb': 'HOSE Weighted P/B',
        'hose_pb': 'HOSE Median P/B',
        'cap_weighted': 'Capitalization-weighted',
        'all_hose': 'All HOSE stocks',
        'vingroup': '🏙️ Vingroup Ecosystem',
        'special_group': 'Special Group',
        'close': 'Close',
        'market_val': '⚖️ Market Valuation',
        'exclude_vin': 'Select sectors to exclude from VN-Index calculation:',
        'sec_wpe': '📊 Sector Weighted P/E',
        'sec_wpb': '📊 Sector Weighted P/B',
        'sec_pe': '📊 Sector Median P/E',
        'pe_legend': 'Green <12 · Blue <20 · Yellow <30 · Red ≥30',
        'sec_pb': '📊 Sector Median P/B',
        'pb_legend': 'Lower = cheaper vs book value',
        'sec_trend': '📈 Sector Trend',
        'sel_display': 'Select sectors to display:',
        'sel_all': '✅ Select all',
        'desel_all': '❌ Deselect all',
        'sel_vnidx': '📈 VN-Index only',
        'trend_msg': 'Trend appears after the second trading day.',
        'all_stocks': '📋 All HOSE Stocks',
        'ticker': 'Ticker',
        'close_vnd': 'Close (VND)',
        'sector': 'Sector',
        'industry': 'Industry',
        'group': 'Group',
        'footer': 'Auto-updated daily via GitHub Actions · vnstock (KBS) · Chart.js',
        'lang_toggle': '🇻🇳 Tiếng Việt',
        'lang_link': 'index-vi.html',
        'metric_wpe': 'Weighted P/E',
        'metric_wpb': 'Weighted P/B',
        'metric_pe': 'Median P/E',
        'metric_pb': 'Median P/B',
        'dt_search': 'Filter:',
        'dt_length': 'Show _MENU_ stocks',
        'dt_all': 'All',
        'data_updated': 'Data updated:',
        'accessed_at': 'Accessed at:'
    },
    'vi': {
        'page_title': 'Định giá P/E & P/B sàn HOSE',
        'title': '📊 Định giá P/E & P/B sàn HOSE',
        'as_of': 'Ngày',
        'valid_stocks': 'Cổ phiếu hợp lệ',
        'out_of': 'trên tổng',
        'light_mode': 'Giao diện sáng',
        'dark_mode': 'Giao diện tối',
        'hose_wpe': 'P/E Trung bình (HOSE)',
        'hose_pe': 'P/E Trung vị (HOSE)',
        'hose_wpb': 'P/B Trung bình (HOSE)',
        'hose_pb': 'P/B Trung vị (HOSE)',
        'cap_weighted': 'Trọng số theo Vốn hóa',
        'all_hose': 'Toàn bộ cổ phiếu HOSE',
        'vingroup': '🏙️ Hệ sinh thái Vingroup',
        'special_group': 'Nhóm Đặc biệt',
        'close': 'Giá',
        'market_val': '⚖️ Định giá Thị trường',
        'exclude_vin': 'Chọn các nhóm ngành để loại trừ khỏi VN-Index:',
        'sec_wpe': '📊 P/E Theo Vốn Hóa',
        'sec_wpb': '📊 P/B Theo Vốn Hóa',
        'sec_pe': '📊 P/E Trung vị Ngành',
        'pe_legend': 'Xanh lá <12 · Xanh dương <20 · Vàng <30 · Đỏ ≥30',
        'sec_pb': '📊 P/B Trung vị Ngành',
        'pb_legend': 'Thấp hơn = rẻ hơn so với giá trị sổ sách',
        'sec_trend': '📈 Xu Hướng Định Giá',
        'sel_display': 'Chọn nhóm ngành hiển thị:',
        'sel_all': '✅ Chọn tất cả',
        'desel_all': '❌ Bỏ chọn tất cả',
        'sel_vnidx': '📈 Chỉ chọn VN-Index',
        'trend_msg': 'Xu hướng sẽ xuất hiện sau ngày giao dịch thứ 2.',
        'all_stocks': '📋 Toàn bộ cổ phiếu HOSE',
        'ticker': 'Mã CP',
        'close_vnd': 'Giá Đóng Cửa (VNĐ)',
        'sector': 'Nhóm Ngành',
        'industry': 'Ngành Nghề',
        'group': 'Nhóm',
        'footer': 'Cập nhật tự động mỗi ngày qua GitHub Actions · vnstock (KBS) · Chart.js',
        'lang_toggle': '🇬🇧 English',
        'lang_link': 'index.html',
        'metric_wpe': 'P/E (Vốn hóa)',
        'metric_wpb': 'P/B (Vốn hóa)',
        'metric_pe': 'P/E (Trung vị)',
        'metric_pb': 'P/B (Trung vị)',
        'dt_search': 'Lọc:',
        'dt_length': 'Hiển thị _MENU_ mã',
        'dt_all': 'Tất cả',
        'data_updated': 'Dữ liệu cập nhật:',
        'accessed_at': 'Truy cập lúc:'
    }
}

def _safe(v):
    if v is None: return None
    try:
        if np.isnan(v) or np.isinf(v): return None
    except (TypeError, ValueError): pass
    if isinstance(v, (np.integer,)): return int(v)
    if isinstance(v, (float, np.floating)): return round(float(v), 2)
    return v

def _records(df): return [{k: _safe(v) for k, v in r.items()} for r in df.to_dict("records")]

def load_data():
    for p in (TICKER_HIST_FILE, SECTOR_HIST_FILE):
        if not Path(p).exists():
            print(f"ERROR: {p} not found. Run daily_compute.py first.")
            sys.exit(1)
    tick_h = pd.read_parquet(TICKER_HIST_FILE)
    sect_h = pd.read_parquet(SECTOR_HIST_FILE)
    tick_h["date"] = pd.to_datetime(tick_h["date"])
    sect_h["date"] = pd.to_datetime(sect_h["date"])
    ld = tick_h["date"].max()
    c5y = ld - timedelta(days=365*5)
    return (tick_h[tick_h["date"] == ld].copy(),
            tick_h[tick_h["date"] >= c5y].copy(),
            sect_h[sect_h["date"] == ld].copy(),
            sect_h[sect_h["date"] >= c5y].copy(),
            ld)

def translate_df(df, lang):
    if lang == "en":
        if "group" in df.columns: df["group"] = df["group"].map(lambda x: SECTOR_TRANSLATION.get(x, x))
        if "sector" in df.columns: df["sector"] = df["sector"].map(lambda x: SECTOR_TRANSLATION.get(x, x))
        if "industry" in df.columns: df["industry"] = df["industry"].map(lambda x: SECTOR_TRANSLATION.get(x, x))
    return df

def build_payload(tl, t5y, sl, s5y, ld, lang):
    tl = translate_df(tl.copy(), lang)
    sl = translate_df(sl.copy(), lang)
    s5y = translate_df(s5y.copy(), lang)

    all_pe = tl["pe"].dropna(); all_pb = tl["pb"].dropna()
    vn_idx = sl[sl["group"] == "VN-Index"].iloc[-1] if not sl[sl["group"] == "VN-Index"].empty else None
    
    m_pe = _safe(vn_idx["median_pe"]) if vn_idx is not None else _safe(all_pe.median())
    m_pb = _safe(vn_idx["median_pb"]) if vn_idx is not None else _safe(all_pb.median())
    w_pe = _safe(vn_idx["weighted_pe"]) if vn_idx is not None else None
    w_pb = _safe(vn_idx["weighted_pb"]) if vn_idx is not None else None

    market = {"date": ld.strftime("%Y-%m-%d"),
              "update_time": datetime.now(timezone.utc).isoformat(),
              "median_pe": m_pe, "median_pb": m_pb,
              "weighted_pe": w_pe, "weighted_pb": w_pb,
              "total": len(tl), "valid_pe": int(all_pe.notna().sum()),
              "valid_pb": int(all_pb.notna().sum())}

    vg = tl[tl["ticker"].isin(VINGROUP_TICKERS)][["ticker","close","pe","pb"]].copy()
    vingroup = _records(vg)

    sect_cols = ["group","count","valid_pe","valid_pb",
                 "median_pe","median_pb","mean_pe","mean_pb",
                 "weighted_pe","weighted_pb","p25_pe","p75_pe","p25_pb","p75_pb"]
    sl_sectors = sl[~sl["group"].isin(["VN-Index", "Unknown"])]
    avail = [c for c in sect_cols if c in sl_sectors.columns]
    sectors = _records(sl_sectors[avail].sort_values("median_pe", na_position="last"))

    trend_groups = s5y[~s5y["group"].isin(["VN-Index", "Unknown"])]["group"].unique().tolist()
    trend_groups.insert(0, "VN-Index")
    trend = {}
    for grp in trend_groups:
        sub = s5y[s5y["group"]==grp].sort_values("date")[["date","median_pe","median_pb","weighted_pe","weighted_pb","sum_pe_mc","sum_pe_ern","sum_pb_mc","sum_pb_bv"]]
        trend[grp] = {"dates": sub["date"].dt.strftime("%Y-%m-%d").tolist(),
                      "pe": [_safe(v) for v in sub["median_pe"]],
                      "pb": [_safe(v) for v in sub["median_pb"]],
                      "wpe": [_safe(v) for v in sub["weighted_pe"]],
                      "wpb": [_safe(v) for v in sub["weighted_pb"]],
                      "mc_pe": [_safe(v) for v in sub["sum_pe_mc"]],
                      "ern_pe": [_safe(v) for v in sub["sum_pe_ern"]],
                      "mc_pb": [_safe(v) for v in sub["sum_pb_mc"]],
                      "bv_pb": [_safe(v) for v in sub["sum_pb_bv"]]}

    tbl = ["ticker","close","pe","pb","sector","industry","group","shares"]
    if "shares" not in tl.columns: tl["shares"] = 0
    tickers = _records(tl[[c for c in tbl if c in tl.columns]].sort_values("pe", na_position="last"))
    return {"market": market, "vingroup": vingroup, "sectors": sectors,
            "trend": trend, "tickers": tickers}

def get_html(lang):
    ui = UI_STR[lang]
    return f"""<!DOCTYPE html>
<html lang="{lang}" data-theme="dark">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{ui['page_title']}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css"/>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
<style>
:root{{--bg:#0f172a;--card:#1e293b;--card2:#0f172a;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;--dim:#64748b;--accent:#38bdf8;--accent2:#818cf8;--grid:#1e3a5f;--hover:#1e3a5f;--vg-bg:#0f172a;--vg-border:#1d4ed8;--btn-bg:#1e293b;--btn-border:#475569;--shadow:rgba(0,0,0,.4)}}
[data-theme="light"]{{--bg:#f1f5f9;--card:#fff;--card2:#f8fafc;--border:#e2e8f0;--text:#1e293b;--muted:#475569;--dim:#94a3b8;--accent:#0284c7;--accent2:#6366f1;--grid:#e2e8f0;--hover:#eff6ff;--vg-bg:#eff6ff;--vg-border:#3b82f6;--btn-bg:#fff;--btn-border:#cbd5e1;--shadow:rgba(0,0,0,.08)}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:ui-sans-serif,system-ui,sans-serif;font-size:14px;transition:background .25s,color .25s}}
.page{{max-width:1280px;margin:0 auto;padding:24px 16px 64px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:20px;box-shadow:0 2px 8px var(--shadow);transition:background .25s,border-color .25s}}
.hdr{{display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:28px}}
.hdr h1{{font-size:1.75rem;font-weight:800}}
.hdr p{{color:var(--muted);font-size:.85rem;margin-top:6px}}
.hl{{color:var(--accent);font-weight:600}}
.theme-btn{{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border:1px solid var(--btn-border);border-radius:8px;background:var(--btn-bg);color:var(--text);font-size:.8rem;font-weight:600;cursor:pointer;transition:background .2s,border-color .2s;text-decoration:none}}
.theme-btn:hover{{border-color:var(--accent);color:var(--accent)}}
.g4{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-bottom:32px}}
.g2{{display:grid;grid-template-columns:1fr;gap:16px;margin-bottom:32px}}
.gvg{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}
@media(min-width:640px){{.g4{{grid-template-columns:repeat(4,1fr)}}.gvg{{grid-template-columns:repeat(4,1fr)}}}}
@media(min-width:1024px){{.g2{{grid-template-columns:repeat(2,1fr)}}}}
.lbl{{color:var(--muted);font-size:.7rem;text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px}}
.big{{font-size:2rem;font-weight:800;color:var(--accent)}}
.sub{{color:var(--dim);font-size:.75rem;margin-top:4px}}
.sec{{font-size:.95rem;font-weight:700;margin-bottom:4px}}
.sec-sub{{color:var(--muted);font-size:.75rem;margin-bottom:16px}}
.mb8{{margin-bottom:32px}}
.vg-card{{background:var(--vg-bg);border:1px solid var(--vg-border);border-radius:12px;padding:14px;transition:background .25s,border-color .25s}}
.vg-badge{{display:inline-block;background:#1d4ed8;color:#fff;font-size:.65rem;font-weight:700;padding:2px 8px;border-radius:999px;margin-left:6px}}
table.dataTable{{background:var(--card)!important;color:var(--text)!important;border-collapse:collapse;width:100%!important}}
table.dataTable thead th{{background:var(--card2)!important;color:var(--muted)!important;border-bottom:1px solid var(--border)!important;padding:10px 12px!important;font-weight:700}}
table.dataTable tbody td{{padding:8px 12px;border-bottom:1px solid var(--border)}}
table.dataTable tbody tr:hover td{{background:var(--hover)!important}}
.dataTables_wrapper .dataTables_filter input,.dataTables_wrapper .dataTables_length select{{background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 10px}}
.dataTables_wrapper .dataTables_filter label,.dataTables_wrapper .dataTables_length label,.dataTables_wrapper .dataTables_info{{color:var(--muted)}}
.dataTables_wrapper .paginate_button{{color:var(--muted)!important;border-radius:6px!important;padding:4px 10px!important}}
.dataTables_wrapper .paginate_button.current{{background:#1d4ed8!important;color:#fff!important}}
.footer{{text-align:center;color:var(--dim);font-size:.75rem;margin-top:48px}}
.ovx{{overflow-x:auto}}
.pill{{display:inline-flex;align-items:center;padding:4px 10px;background:var(--card2);border:1px solid var(--border);border-radius:999px;font-size:.8rem;color:var(--text);cursor:pointer;user-select:none;transition:all .2s}}
.pill:hover{{border-color:var(--accent);color:var(--accent)}}
.pill.active{{background:var(--accent);border-color:var(--accent);color:#fff}}
</style>
</head>
<body>
<div class="page">
  <header class="hdr">
    <div>
      <h1>{ui['title']}</h1>
      <p>{ui['as_of']} <span class="hl" id="hdr-date"></span> &nbsp;·&nbsp; PE = Close / TTM EPS &nbsp; PB = Close / BVPS &nbsp;·&nbsp; {ui['valid_stocks']}: <span class="hl" id="mkt-npe"></span> PE / <span class="hl" id="mkt-npb"></span> PB {ui['out_of']} <span id="mkt-tot"></span></p>
      <p>{ui['data_updated']} <span class="hl" id="update-time"></span> &nbsp;·&nbsp; {ui['accessed_at']} <span class="hl" id="access-time"></span></p>
    </div>
    <div style="display:flex;gap:10px;align-items:center;">
      <div id="live-clock" style="font-family:monospace;font-size:1.1rem;font-weight:700;color:var(--accent);background:var(--card2);padding:6px 12px;border-radius:8px;border:1px solid var(--border);box-shadow:inset 0 2px 4px rgba(0,0,0,0.1);">--:--:--</div>
      <a href="{ui['lang_link']}" class="theme-btn" style="text-decoration:none;">{ui['lang_toggle']}</a>
      <button class="theme-btn" onclick="toggleTheme()">
        <span id="theme-icon">☀️</span><span id="theme-label">{ui['light_mode']}</span>
      </button>
    </div>
  </header>

  <div class="g4">
    <div class="card"><div class="lbl">{ui['hose_wpe']}</div><div class="big" id="mkt-wpe">—</div><div class="sub">{ui['cap_weighted']}</div></div>
    <div class="card"><div class="lbl">{ui['hose_pe']}</div><div class="big" id="mkt-pe">—</div><div class="sub">{ui['all_hose']}</div></div>
    <div class="card"><div class="lbl">{ui['hose_wpb']}</div><div class="big" id="mkt-wpb">—</div><div class="sub">{ui['cap_weighted']}</div></div>
    <div class="card"><div class="lbl">{ui['hose_pb']}</div><div class="big" id="mkt-pb">—</div><div class="sub">{ui['all_hose']}</div></div>
  </div>

  <div class="card mb8">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
      <span class="sec">{ui['vingroup']}</span><span class="vg-badge">{ui['special_group']}</span>
    </div>
    <div class="gvg" id="vg-cards"></div>
  </div>
  
  <div class="card mb8">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <div class="sec" style="margin:0">{ui['market_val']}</div>
      <div id="tf-pills" style="display:flex; gap:4px; font-size:0.75rem;"></div>
    </div>
    <div class="sec-sub" style="margin-top:8px">{ui['exclude_vin']}</div>
    <div id="sector-pills" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;"></div>
    <div style="position: relative; height: 350px; width: 100%;">
      <canvas id="chart-ex-vin"></canvas>
    </div>
  </div>

  <div class="g2">
    <div class="card"><div class="sec">{ui['sec_wpe']}</div><div class="sec-sub">{ui['cap_weighted']}</div><div style="position: relative; height: 450px; width: 100%;"><canvas id="chart-wpe"></canvas></div></div>
    <div class="card"><div class="sec">{ui['sec_wpb']}</div><div class="sec-sub">{ui['cap_weighted']}</div><div style="position: relative; height: 450px; width: 100%;"><canvas id="chart-wpb"></canvas></div></div>
  </div>

  <div class="g2">
    <div class="card"><div class="sec">{ui['sec_pe']}</div><div class="sec-sub">{ui['pe_legend']}</div><div style="position: relative; height: 450px; width: 100%;"><canvas id="chart-pe"></canvas></div></div>
    <div class="card"><div class="sec">{ui['sec_pb']}</div><div class="sec-sub">{ui['pb_legend']}</div><div style="position: relative; height: 450px; width: 100%;"><canvas id="chart-pb"></canvas></div></div>
  </div>

  <div class="card mb8">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <div class="sec" style="margin:0">{ui['sec_trend']}</div>
      <div style="display:flex; gap: 8px;">
        <div id="trend-metric-pills" style="display:flex; gap:4px; font-size:0.75rem;"></div>
        <div id="trend-tf-pills" style="display:flex; gap:4px; font-size:0.75rem;"></div>
      </div>
    </div>
    <div class="sec-sub" style="margin-top:8px">{ui['sel_display']}</div>
    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap: wrap;">
      <div id="btn-sel-all" class="pill" style="cursor:pointer">{ui['sel_all']}</div>
      <div id="btn-desel-all" class="pill" style="cursor:pointer">{ui['desel_all']}</div>
      <div id="btn-sel-vnidx" class="pill" style="cursor:pointer">{ui['sel_vnidx']}</div>
    </div>
    <div id="trend-pills" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;"></div>
    <div style="position: relative; height: 350px; width: 100%;">
      <canvas id="chart-trend"></canvas>
    </div>
    <p id="trend-msg" style="color:var(--dim);font-size:.75rem;margin-top:8px"></p>
  </div>

  <div class="card">
    <div class="sec" style="margin-bottom:16px">{ui['all_stocks']}</div>
    <div class="ovx">
      <table id="tbl" class="display compact nowrap" style="width:100%">
        <thead><tr><th>{ui['ticker']}</th><th>{ui['close_vnd']}</th><th>P/E</th><th>P/B</th><th>{ui['sector']}</th><th>{ui['industry']}</th><th>{ui['group']}</th></tr></thead>
        <tbody id="tbl-body"></tbody>
      </table>
    </div>
  </div>
  <p class="footer">{ui['footer']}</p>
</div>

<script>
const D=__DATA_JSON__;
const charts={{}};
const PAL=['#38bdf8','#818cf8','#34d399','#fbbf24','#f87171','#a78bfa','#fb923c','#4ade80','#f472b6','#2dd4bf','#c084fc','#fcd34d'];
function peCol(v){{const dk=document.documentElement.getAttribute('data-theme')==='dark';if(v==null)return dk?'#475569':'#94a3b8';if(v<12)return dk?'#4ade80':'#16a34a';if(v<20)return dk?'#38bdf8':'#0284c7';if(v<30)return dk?'#facc15':'#b45309';return dk?'#f87171':'#dc2626';}}
function themeC(t){{const dk=t==='dark';return{{grid:dk?'#1e3a5f':'#e2e8f0',ticks:dk?'#94a3b8':'#475569',leg:dk?'#94a3b8':'#475569'}}}}
function applyChartTheme(t){{const c=themeC(t);Object.values(charts).forEach(ch=>{{if(!ch)return;['x','y'].forEach(ax=>{{const s=ch.options.scales?.[ax];if(s){{s.grid.color=c.grid;s.ticks.color=c.ticks;}}}});const l=ch.options.plugins?.legend?.labels;if(l)l.color=c.leg;ch.update('none');}});}}
function setTheme(t){{document.documentElement.setAttribute('data-theme',t);localStorage.setItem('vn-pe-pb-theme',t);const dk=t==='dark';document.getElementById('theme-icon').textContent=dk?'☀️':'🌙';document.getElementById('theme-label').textContent=dk?'{ui['light_mode']}':'{ui['dark_mode']}';applyChartTheme(t);}}
function toggleTheme(){{setTheme(document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark');}}
const _it=localStorage.getItem('vn-pe-pb-theme')||(window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
document.documentElement.setAttribute('data-theme',_it);
function fmt(v,dp=2){{return v==null?'—':(+v).toFixed(dp);}}
function fmtK(v){{return v==null?'—':(v/1000).toFixed(1)+'K';}}
function formatTime(date, lang) {{
  const h = String(date.getHours()).padStart(2, '0');
  const m = String(date.getMinutes()).padStart(2, '0');
  const s = String(date.getSeconds()).padStart(2, '0');
  if (lang === 'vi') {{
    const days = ['Chủ nhật', 'Thứ hai', 'Thứ ba', 'Thứ tư', 'Thứ năm', 'Thứ sáu', 'Thứ bảy'];
    return `${{h}}:${{m}}:${{s}} ${{days[date.getDay()]}}`;
  }} else {{
    const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    return `${{h}}:${{m}}:${{s}} ${{days[date.getDay()]}}`;
  }}
}}
document.addEventListener('DOMContentLoaded',()=>{{
  const dk=_it==='dark';document.getElementById('theme-icon').textContent=dk?'☀️':'🌙';document.getElementById('theme-label').textContent=dk?'{ui['light_mode']}':'{ui['dark_mode']}';
  const m=D.market;
  document.getElementById('hdr-date').textContent=m.date;
  document.getElementById('mkt-wpe').textContent=fmt(m.weighted_pe);
  document.getElementById('mkt-pe').textContent=fmt(m.median_pe);
  document.getElementById('mkt-wpb').textContent=fmt(m.weighted_pb);
  document.getElementById('mkt-pb').textContent=fmt(m.median_pb);
  document.getElementById('mkt-npe').textContent=m.valid_pe;
  document.getElementById('mkt-npb').textContent=m.valid_pb;
  document.getElementById('mkt-tot').textContent=`${{m.total}}`;

  const lang = document.documentElement.lang;
  const updateDate = new Date(m.update_time);
  document.getElementById('update-time').textContent = formatTime(updateDate, lang);
  
  const accessTimeEl = document.getElementById('access-time');
  accessTimeEl.textContent = formatTime(new Date(), lang);
  
  const liveClockEl = document.getElementById('live-clock');
  liveClockEl.textContent = formatTime(new Date(), lang);
  setInterval(() => {{ liveClockEl.textContent = formatTime(new Date(), lang); }}, 1000);

  const vgEl=document.getElementById('vg-cards');
  D.vingroup.forEach(v=>{{const d=document.createElement('div');d.className='vg-card';d.innerHTML=`<div style="color:var(--accent);font-size:1.2rem;font-weight:800">${{v.ticker}}</div><div style="color:var(--muted);font-size:.8rem;margin-top:5px">{ui['close']}: <span style="color:var(--text);font-weight:700">${{fmtK(v.close)}}</span></div><div style="color:var(--muted);font-size:.8rem">P/E: <span style="color:${{peCol(v.pe)}};font-weight:700">${{fmt(v.pe)}}</span></div><div style="color:var(--muted);font-size:.8rem">P/B: <span style="color:var(--accent2);font-weight:700">${{fmt(v.pb)}}</span></div>`;vgEl.appendChild(d);}});
  const tc=themeC(_it);
  const barOpts=()=>({{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{grid:{{color:tc.grid}},ticks:{{color:tc.ticks}}}},y:{{grid:{{color:tc.grid}},ticks:{{color:tc.ticks,font:{{size:11}},autoSkip:false}}}}}}}});
  const sects=D.sectors.filter(s=>s.median_pe!=null).sort((a,b)=>a.median_pe-b.median_pe);
  charts.pe=new Chart(document.getElementById('chart-pe'),{{type:'bar',data:{{labels:sects.map(s=>s.group),datasets:[{{data:sects.map(s=>s.median_pe),backgroundColor:sects.map(s=>peCol(s.median_pe)),borderRadius:5}}]}},options:{{...barOpts(),plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>` P/E: ${{ctx.parsed.x.toFixed(1)}}`}}}}}}}}}});
  charts.pb=new Chart(document.getElementById('chart-pb'),{{type:'bar',data:{{labels:sects.map(s=>s.group),datasets:[{{data:sects.map(s=>s.median_pb),backgroundColor:'#818cf8',borderRadius:5}}]}},options:{{...barOpts(),plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>` P/B: ${{ctx.parsed.x.toFixed(2)}}`}}}}}}}}}});
  
  const sectsWpe=D.sectors.filter(s=>s.weighted_pe!=null).sort((a,b)=>a.weighted_pe-b.weighted_pe);
  charts.wpe=new Chart(document.getElementById('chart-wpe'),{{type:'bar',data:{{labels:sectsWpe.map(s=>s.group),datasets:[{{data:sectsWpe.map(s=>s.weighted_pe),backgroundColor:sectsWpe.map(s=>peCol(s.weighted_pe)),borderRadius:5}}]}},options:{{...barOpts(),plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>` P/E: ${{ctx.parsed.x.toFixed(1)}}`}}}}}}}}}});
  charts.wpb=new Chart(document.getElementById('chart-wpb'),{{type:'bar',data:{{labels:sectsWpe.map(s=>s.group),datasets:[{{data:sectsWpe.map(s=>s.weighted_pb),backgroundColor:'#34d399',borderRadius:5}}]}},options:{{...barOpts(),plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>` P/B: ${{ctx.parsed.x.toFixed(2)}}`}}}}}}}}}});

  const groups = [...new Set(D.tickers.map(t=>t.group).filter(Boolean))].sort();
  const pillsEl = document.getElementById('sector-pills');
  const selGroups = new Set();
  
  groups.forEach(g => {{
    const lbl = document.createElement('label');
    lbl.className = 'pill' + (selGroups.has(g) ? ' active' : '');
    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.style.display = 'none';
    chk.checked = selGroups.has(g);
    chk.onchange = (e) => {{
      if(e.target.checked) {{ selGroups.add(g); lbl.classList.add('active'); }}
      else {{ selGroups.delete(g); lbl.classList.remove('active'); }}
      window.updateMarketExChart();
    }};
    lbl.appendChild(chk);
    lbl.appendChild(document.createTextNode(g));
    pillsEl.appendChild(lbl);
  }});

  charts.ex_vin = new Chart(document.getElementById('chart-ex-vin'), {{
    type:'line',
    data:{{
      labels:D.trend['VN-Index'].dates, 
      datasets:[
        {{label:'VN-Index W P/E', data:D.trend['VN-Index'].wpe, yAxisID:'y', borderColor:'#38bdf8', backgroundColor:'transparent', tension:.35, pointRadius:0, borderWidth:2}},
        {{label:'VN-Index W P/E (Excl)', data:[], yAxisID:'y', borderColor:'#818cf8', backgroundColor:'transparent', tension:.35, pointRadius:0, borderWidth:2}},
        {{label:'VN-Index W P/B', data:D.trend['VN-Index'].wpb, yAxisID:'y2', borderColor:'#34d399', backgroundColor:'transparent', tension:.35, pointRadius:0, borderWidth:2}},
        {{label:'VN-Index W P/B (Excl)', data:[], yAxisID:'y2', borderColor:'#fbbf24', backgroundColor:'transparent', tension:.35, pointRadius:0, borderWidth:2}}
      ]
    }},
    options:{{
      responsive:true,maintainAspectRatio:false,
      plugins:{{legend:{{labels:{{color:tc.leg,font:{{size:11}},boxWidth:14}}}}}},
      scales:{{
        x:{{type:'category',ticks:{{color:tc.ticks,maxRotation:45,autoSkip:true,maxTicksLimit:15}},grid:{{color:tc.grid}}}},
        y:{{type:'linear',display:true,position:'left',title:{{display:true,text:'P/E',color:tc.ticks}},grid:{{color:tc.grid}},ticks:{{color:tc.ticks}}}},
        y2:{{type:'linear',display:true,position:'right',title:{{display:true,text:'P/B',color:tc.ticks}},grid:{{drawOnChartArea:false}},ticks:{{color:tc.ticks}}}}
      }}
    }}
  }});

  let curTf = '5Y';
  window.updateMarketExChart = function() {{
    let dates = D.trend['VN-Index'].dates;
    let cutoff = '1970-01-01';
    
    // Use the maximum date in the dataset as the reference point
    const maxDateStr = dates.length > 0 ? dates[dates.length - 1] : new Date().toISOString().split('T')[0];
    const d = new Date(maxDateStr);
    
    if(curTf === '3M') {{ d.setMonth(d.getMonth()-3); cutoff = d.toISOString().split('T')[0]; }}
    else if(curTf === 'YTD') {{ d.setMonth(0, 1); cutoff = d.toISOString().split('T')[0]; }}
    else if(curTf === '1Y') {{ d.setFullYear(d.getFullYear()-1); cutoff = d.toISOString().split('T')[0]; }}
    else if(curTf === '3Y') {{ d.setFullYear(d.getFullYear()-3); cutoff = d.toISOString().split('T')[0]; }}
    else if(curTf === '5Y') {{ d.setFullYear(d.getFullYear()-5); cutoff = d.toISOString().split('T')[0]; }}
    
    let startIndex = dates.findIndex(dt => dt >= cutoff);
    if(startIndex === -1) startIndex = 0;
    
    const sliceDates = dates.slice(startIndex);
    const peData = []; const pbData = [];
    const basePe = []; const basePb = [];
    
    sliceDates.forEach((dt, si) => {{
      const i = startIndex + si;
      let m_pe = D.trend['VN-Index'].mc_pe[i] || 0; let e_pe = D.trend['VN-Index'].ern_pe[i] || 0;
      let m_pb = D.trend['VN-Index'].mc_pb[i] || 0; let b_pb = D.trend['VN-Index'].bv_pb[i] || 0;
      
      basePe.push(D.trend['VN-Index'].wpe[i]);
      basePb.push(D.trend['VN-Index'].wpb[i]);
      
      selGroups.forEach(g => {{
        if(D.trend[g]) {{
          const idx = D.trend[g].dates.indexOf(dt);
          if (idx >= 0) {{
            m_pe -= (D.trend[g].mc_pe[idx] || 0); e_pe -= (D.trend[g].ern_pe[idx] || 0);
            m_pb -= (D.trend[g].mc_pb[idx] || 0); b_pb -= (D.trend[g].bv_pb[idx] || 0);
          }}
        }}
      }});
      peData.push(e_pe > 0 ? m_pe / e_pe : null);
      pbData.push(b_pb > 0 ? m_pb / b_pb : null);
    }});
    
    if (charts.ex_vin) {{
      charts.ex_vin.data.labels = sliceDates;
      charts.ex_vin.data.datasets[0].data = basePe;
      charts.ex_vin.data.datasets[1].data = peData;
      charts.ex_vin.data.datasets[2].data = basePb;
      charts.ex_vin.data.datasets[3].data = pbData;
      charts.ex_vin.update();
    }}
  }};

  const tfEl = document.getElementById('tf-pills');
  ['5Y', '3Y', '1Y', 'YTD', '3M'].forEach(tf => {{
    const b = document.createElement('div');
    b.className = 'pill' + (curTf === tf ? ' active' : '');
    b.textContent = tf;
    b.style.cursor = 'pointer';
    b.onclick = () => {{
      curTf = tf;
      Array.from(tfEl.children).forEach(c => c.classList.remove('active'));
      b.classList.add('active');
      window.updateMarketExChart();
    }};
    tfEl.appendChild(b);
  }});

  window.updateMarketExChart();

  const tG=Object.keys(D.trend);
  if(tG.length>0){{
    const allDates = [...new Set(tG.flatMap(g => D.trend[g].dates))].sort();
    
    let curMetric = 'wpe';
    const metricConfig = {{
      'wpe': {{ label: '{ui['metric_wpe']}', key: 'wpe' }},
      'wpb': {{ label: '{ui['metric_wpb']}', key: 'wpb' }},
      'pe': {{ label: '{ui['metric_pe']}', key: 'pe' }},
      'pb': {{ label: '{ui['metric_pb']}', key: 'pb' }}
    }};
    const getTrendData = (metricKey) => tG.map(g => allDates.map(d=>{{const idx = D.trend[g].dates.indexOf(d); return idx>=0 ? D.trend[g][metricKey][idx] : null;}}));
    let fullTrendData = getTrendData(curMetric);

    const trendDS = tG.map((g,i)=>({{
      label:g,
      data:fullTrendData[i].slice(),
      borderColor:PAL[i%PAL.length],
      backgroundColor:'transparent',
      tension:.35,
      pointRadius:0,
      borderWidth:2,
      hidden: g !== 'VN-Index'
    }}));
    charts.trend=new Chart(document.getElementById('chart-trend'),{{type:'line',data:{{labels:allDates.slice(), datasets:trendDS}},options:{{responsive:true,maintainAspectRatio:false,scales:{{x:{{type:'category',ticks:{{color:tc.ticks,maxRotation:45,autoSkip:true,maxTicksLimit:15}},grid:{{color:tc.grid}}}},y:{{title:{{display:true,text:metricConfig[curMetric].label,color:tc.ticks}},grid:{{color:tc.grid}},ticks:{{color:tc.ticks}}}}}},plugins:{{legend:{{display:false}}}}}}}});

    const metricPillsEl = document.getElementById('trend-metric-pills');
    ['wpe', 'wpb', 'pe', 'pb'].forEach(mKey => {{
      const b = document.createElement('div');
      b.className = 'pill' + (curMetric === mKey ? ' active' : '');
      b.textContent = metricConfig[mKey].label;
      b.style.cursor = 'pointer';
      b.onclick = () => {{
        curMetric = mKey;
        Array.from(metricPillsEl.children).forEach(c => c.classList.remove('active'));
        b.classList.add('active');
        fullTrendData = getTrendData(mKey);
        charts.trend.options.scales.y.title.text = metricConfig[mKey].label;
        updateTrendTimeframe();
      }};
      metricPillsEl.appendChild(b);
    }});

    const trendPillsEl = document.getElementById('trend-pills');
    const trendCheckboxes = [];
    tG.forEach((g, i) => {{
      const lbl = document.createElement('label');
      lbl.className = 'pill' + (g === 'VN-Index' ? ' active' : '');
      const chk = document.createElement('input');
      chk.type = 'checkbox'; chk.style.display = 'none'; chk.checked = g === 'VN-Index';
      chk.onchange = (e) => {{
        const ds = charts.trend.data.datasets[i];
        if(e.target.checked) {{ ds.hidden = false; lbl.classList.add('active'); }}
        else {{ ds.hidden = true; lbl.classList.remove('active'); }}
        charts.trend.update();
      }};
      trendCheckboxes.push({{chk, lbl, dsIndex: i}});
      lbl.appendChild(chk);
      const swatch = document.createElement('span');
      swatch.style.display = 'inline-block'; swatch.style.width = '8px'; swatch.style.height = '8px';
      swatch.style.borderRadius = '50%'; swatch.style.marginRight = '6px';
      swatch.style.backgroundColor = PAL[i%PAL.length];
      lbl.appendChild(swatch);
      lbl.appendChild(document.createTextNode(g));
      trendPillsEl.appendChild(lbl);
    }});

    document.getElementById('btn-sel-all').onclick = () => {{
      trendCheckboxes.forEach(item => {{
        item.chk.checked = true; item.lbl.classList.add('active'); charts.trend.data.datasets[item.dsIndex].hidden = false;
      }});
      charts.trend.update();
    }};
    document.getElementById('btn-desel-all').onclick = () => {{
      trendCheckboxes.forEach(item => {{
        item.chk.checked = false; item.lbl.classList.remove('active'); charts.trend.data.datasets[item.dsIndex].hidden = true;
      }});
      charts.trend.update();
    }};
    document.getElementById('btn-sel-vnidx').onclick = () => {{
      trendCheckboxes.forEach(item => {{
        const isVN = tG[item.dsIndex] === 'VN-Index';
        item.chk.checked = isVN;
        if(isVN) item.lbl.classList.add('active'); else item.lbl.classList.remove('active');
        charts.trend.data.datasets[item.dsIndex].hidden = !isVN;
      }});
      charts.trend.update();
    }};

    let trendTf = '5Y';
    const updateTrendTimeframe = () => {{
      let cutoff = '1970-01-01';
      const maxDateStr = allDates.length > 0 ? allDates[allDates.length - 1] : new Date().toISOString().split('T')[0];
      const d = new Date(maxDateStr);
      if(trendTf === '3M') {{ d.setMonth(d.getMonth()-3); cutoff = d.toISOString().split('T')[0]; }}
      else if(trendTf === 'YTD') {{ d.setMonth(0, 1); cutoff = d.toISOString().split('T')[0]; }}
      else if(trendTf === '1Y') {{ d.setFullYear(d.getFullYear()-1); cutoff = d.toISOString().split('T')[0]; }}
      else if(trendTf === '3Y') {{ d.setFullYear(d.getFullYear()-3); cutoff = d.toISOString().split('T')[0]; }}
      else if(trendTf === '5Y') {{ d.setFullYear(d.getFullYear()-5); cutoff = d.toISOString().split('T')[0]; }}
      
      let startIndex = allDates.findIndex(dt => dt >= cutoff);
      if(startIndex === -1) startIndex = 0;
      
      charts.trend.data.labels = allDates.slice(startIndex);
      charts.trend.data.datasets.forEach((ds, i) => {{
        ds.data = fullTrendData[i].slice(startIndex);
      }});
      charts.trend.update();
    }};

    const trendTfEl = document.getElementById('trend-tf-pills');
    ['5Y', '3Y', '1Y', 'YTD', '3M'].forEach(tf => {{
      const b = document.createElement('div');
      b.className = 'pill' + (trendTf === tf ? ' active' : '');
      b.textContent = tf;
      b.style.cursor = 'pointer';
      b.onclick = () => {{
        trendTf = tf;
        Array.from(trendTfEl.children).forEach(c => c.classList.remove('active'));
        b.classList.add('active');
        updateTrendTimeframe();
      }};
      trendTfEl.appendChild(b);
    }});
    updateTrendTimeframe();

  }}else{{
    document.getElementById('trend-msg').textContent='{ui['trend_msg']}';
  }}
  const tbody=document.getElementById('tbl-body');
  D.tickers.forEach(t=>{{const isVG=['VIC','VHM','VRE','VPL'].includes(t.ticker);const tr=document.createElement('tr');tr.innerHTML=`<td>${{isVG?`<span style="color:var(--accent);font-weight:700">${{t.ticker}}</span> <span style="color:#3b82f6;font-size:.65rem">VG</span>`:`<span style="font-weight:600">${{t.ticker}}</span>`}}</td><td data-sort="${{t.close||0}}">${{fmtK(t.close)}}</td><td data-sort="${{t.pe==null?999999:t.pe}}">${{t.pe==null?'—':`<span style="color:${{peCol(t.pe)}};font-weight:700">${{fmt(t.pe)}}</span>`}}</td><td data-sort="${{t.pb==null?999999:t.pb}}">${{t.pb==null?'—':`<span style="color:var(--accent2);font-weight:700">${{fmt(t.pb)}}</span>`}}</td><td style="color:var(--muted)">${{t.sector||'—'}}</td><td style="color:var(--dim);font-size:.8rem">${{t.industry||'—'}}</td><td style="color:var(--muted)">${{t.group||'—'}}</td>`;tbody.appendChild(tr);}});
  $('#tbl').DataTable({{
    pageLength:25, order:[[2,'asc']], columnDefs:[{{targets:[1,2,3],type:'num'}}],
    language:{{search:'{ui['dt_search']}',lengthMenu:'{ui['dt_length']}'}},
    initComplete: function () {{
      this.api().columns([4, 5, 6]).every(function () {{
        var column = this;
        var select = $('<select style="display:block;margin-top:6px;width:100%;padding:4px;border-radius:4px;background:var(--card2);color:var(--text);border:1px solid var(--border);font-weight:normal;font-size:0.8rem;"><option value="">{ui['dt_all']}</option></select>')
          .appendTo($(column.header()))
          .on('change', function () {{
            var val = $.fn.dataTable.util.escapeRegex($(this).val());
            column.search(val ? '^' + val + '$' : '', true, false).draw();
          }});
        column.data().unique().sort().each(function (d, j) {{
          if(d && d!=='—') {{
            var text = $('<div>').html(d).text();
            select.append('<option value="' + text + '">' + text + '</option>')
          }}
        }});
        $(select).on('click', function(e) {{ e.stopPropagation(); }});
      }});
    }}
  }});
  applyChartTheme(_it);
}});
</script>
</body>
</html>"""


def build():
    Path(DOCS_DIR).mkdir(parents=True, exist_ok=True)
    tl, t5y, sl, s5y, ld = load_data()
    
    # 1) Build English version
    payload_en = build_payload(tl, t5y, sl, s5y, ld, "en")
    json_en = Path(DOCS_DIR) / "data_latest.json"
    with open(json_en, "w", encoding="utf-8") as f:
        json.dump(payload_en, f, ensure_ascii=False, indent=2, default=str)
    
    html_en = get_html("en").replace("__DATA_JSON__", json.dumps(payload_en, ensure_ascii=False, default=str))
    file_en = Path(DOCS_DIR) / "index.html"
    with open(file_en, "w", encoding="utf-8") as f:
        f.write(html_en)
    print(f"EN Dashboard built → {file_en}")

    # 2) Build Vietnamese version
    payload_vi = build_payload(tl, t5y, sl, s5y, ld, "vi")
    json_vi = Path(DOCS_DIR) / "data_latest_vi.json"
    with open(json_vi, "w", encoding="utf-8") as f:
        json.dump(payload_vi, f, ensure_ascii=False, indent=2, default=str)
        
    html_vi = get_html("vi").replace("__DATA_JSON__", json.dumps(payload_vi, ensure_ascii=False, default=str))
    file_vi = Path(DOCS_DIR) / "index-vi.html"
    with open(file_vi, "w", encoding="utf-8") as f:
        f.write(html_vi)
    print(f"VI Dashboard built → {file_vi}")


if __name__ == "__main__":
    build()

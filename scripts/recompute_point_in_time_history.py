#!/usr/bin/env python3
"""
Recompute 5-year historical P/E and P/B using Estimated Point-in-Time Fundamentals.
Vnstock 4.0 compatible.
"""
import json, logging, sys, time
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scripts.config import (
    TICKER_HIST_FILE, SECTOR_HIST_FILE, FUND_FILE,
    PE_MIN, PE_MAX, PB_MIN, PB_MAX,
    VINGROUP_TICKERS, VINGROUP_GROUP
)
from scripts.recompute_history_clean import aggregate_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("point_in_time")

CACHE_FILE = PROJECT_ROOT / "history" / "point_in_time_cache.json"

def get_cutoff_date_for_period(period_str: str) -> str:
    period_str = str(period_str).strip().upper()
    if "-Q" in period_str:
        parts = period_str.split("-Q"); year = int(parts[0]); q = int(parts[1])
        if q == 1: return f"{year}-05-01"
        elif q == 2: return f"{year}-08-01"
        elif q == 3: return f"{year}-11-01"
        elif q == 4: return f"{year + 1}-02-15"
    else:
        try: return f"{int(period_str) + 1}-04-01"
        except ValueError: pass
    return "1970-01-01"

def fetch_timeline_for_ticker(ticker: str, shares: float, stock) -> dict:
    eps_points = []; bvps_points = []
    if not (pd.notna(shares) and shares > 0): return {"eps": [], "bvps": []}

    fin = stock.finance
    # 1. Annual
    try:
        is_yr = fin.income_statement(period="year", lang="en")
        if is_yr is not None and not is_yr.empty:
            meta_cols = {"item", "item_en", "item_id", "period"}
            val_cols = [c for c in is_yr.columns if c not in meta_cols]
            pat = is_yr[is_yr["item_id"].astype(str).str.lower() == "isa22"]
            if not pat.empty:
                for col in val_cols:
                    v = pd.to_numeric(pat[col].iloc[0], errors="coerce")
                    if pd.notna(v) and v != 0:
                        cutoff = get_cutoff_date_for_period(col)
                        if cutoff != "1970-01-01": eps_points.append((cutoff, float(v / shares)))
    except Exception as exc: log.debug(f"  {ticker} annual income failed: {exc}")

    try:
        bs_yr = fin.balance_sheet(period="year", lang="en")
        if bs_yr is not None and not bs_yr.empty:
            meta_cols = {"item", "item_en", "item_id", "period"}
            val_cols = [c for c in bs_yr.columns if c not in meta_cols]
            eq = bs_yr[bs_yr["item_id"].astype(str).str.lower() == "bsa78"]
            if not eq.empty:
                for col in val_cols:
                    v = pd.to_numeric(eq[col].iloc[0], errors="coerce")
                    if pd.notna(v) and v != 0:
                        cutoff = get_cutoff_date_for_period(col)
                        if cutoff != "1970-01-01": bvps_points.append((cutoff, float(v / shares)))
    except Exception as exc: log.debug(f"  {ticker} annual balance sheet failed: {exc}")

    # 2. Quarterly
    try:
        is_q = fin.income_statement(period="quarter", lang="en")
        if is_q is not None and not is_q.empty:
            meta_cols = {"item", "item_en", "item_id", "period"}
            val_cols = [c for c in is_q.columns if c not in meta_cols]
            val_cols_sorted = sorted(val_cols)
            pat = is_q[is_q["item_id"].astype(str).str.lower() == "isa22"]
            if not pat.empty and len(val_cols_sorted) >= 4:
                for i in range(3, len(val_cols_sorted)):
                    win = val_cols_sorted[i-3:i+1]
                    vals = pd.to_numeric(pat[win].iloc[0], errors="coerce")
                    if vals.notna().sum() == 4:
                        ttm_profit = float(vals.sum())
                        cutoff = get_cutoff_date_for_period(val_cols_sorted[i])
                        if cutoff != "1970-01-01": eps_points.append((cutoff, float(ttm_profit / shares)))
    except Exception as exc: log.debug(f"  {ticker} quarterly income failed: {exc}")

    try:
        bs_q = fin.balance_sheet(period="quarter", lang="en")
        if bs_q is not None and not bs_q.empty:
            meta_cols = {"item", "item_en", "item_id", "period"}
            val_cols = [c for c in bs_q.columns if c not in meta_cols]
            eq = bs_q[bs_q["item_id"].astype(str).str.lower() == "bsa78"]
            if not eq.empty:
                for col in val_cols:
                    v = pd.to_numeric(eq[col].iloc[0], errors="coerce")
                    if pd.notna(v) and v != 0:
                        cutoff = get_cutoff_date_for_period(col)
                        if cutoff != "1970-01-01": bvps_points.append((cutoff, float(v / shares)))
    except Exception as exc: log.debug(f"  {ticker} quarterly balance sheet failed: {exc}")

    eps_dict = {dt: val for dt, val in sorted(eps_points)}
    bvps_dict = {dt: val for dt, val in sorted(bvps_points)}
    return {"eps": sorted([list(item) for item in eps_dict.items()]), "bvps": sorted([list(item) for item in bvps_dict.items()])}

def main():
    log.info("Starting Estimated Point-in-Time Historical Fundamentals backfill...")
    if not Path(TICKER_HIST_FILE).exists() or not Path(FUND_FILE).exists():
        log.error("TICKER_HIST_FILE or FUND_FILE not found."); sys.exit(1)

    tick = pd.read_parquet(TICKER_HIST_FILE); fund = pd.read_parquet(FUND_FILE)
    if "ticker" not in fund.columns: fund = fund.reset_index()
    fund_map = fund.set_index("ticker").to_dict("index")
    unique_tickers = sorted(tick["ticker"].unique())

    cache = {}
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f: cache = json.load(f)
        except Exception: pass

    from vnstock import Vnstock
    tickers_needed = [t for t in unique_tickers if t not in cache]
    
    for idx, ticker in enumerate(tickers_needed, 1):
        shares = fund_map.get(ticker, {}).get("shares", np.nan)
        try:
            stock = Vnstock().stock(symbol=ticker, source="VCI")
            cache[ticker] = fetch_timeline_for_ticker(ticker, shares, stock)
        except Exception:
            cache[ticker] = {"eps": [], "bvps": []}
        if idx % 15 == 0 or idx == len(tickers_needed):
            with open(CACHE_FILE, "w", encoding="utf-8") as f: json.dump(cache, f, ensure_ascii=False)
        time.sleep(0.12)

    tick["date"] = pd.to_datetime(tick["date"])
    tick = tick.sort_values(["ticker", "date"]).reset_index(drop=True)
    if "shares" not in tick.columns: tick["shares"] = np.nan
    for col in ["eps_ttm", "bvps", "pe", "pb"]: tick[col] = np.nan

    dfs = []
    for ticker, grp in tick.groupby("ticker", sort=False):
        grp = grp.sort_values("date").copy()
        t_info = fund_map.get(ticker, {})
        if pd.isna(grp["shares"].iloc[0]) and pd.notna(t_info.get("shares")): grp["shares"] = t_info["shares"]
        
        t_cache = cache.get(ticker, {"eps": [], "bvps": []})
        eps_list = t_cache.get("eps", []); bvps_list = t_cache.get("bvps", [])
        
        if eps_list:
            eps_df = pd.DataFrame(eps_list, columns=["date_eff", "eps_val"]); eps_df["date_eff"] = pd.to_datetime(eps_df["date_eff"])
            grp = pd.merge_asof(grp, eps_df.sort_values("date_eff"), left_on="date", right_on="date_eff", direction="backward")
            grp["eps_ttm"] = grp["eps_val"].bfill().fillna(t_info.get("eps_ttm", np.nan))
            grp = grp.drop(columns=["date_eff", "eps_val"], errors="ignore")
        else: grp["eps_ttm"] = t_info.get("eps_ttm", np.nan)

        if bvps_list:
            bvps_df = pd.DataFrame(bvps_list, columns=["date_eff", "bvps_val"]); bvps_df["date_eff"] = pd.to_datetime(bvps_df["date_eff"])
            grp = pd.merge_asof(grp, bvps_df.sort_values("date_eff"), left_on="date", right_on="date_eff", direction="backward")
            grp["bvps"] = grp["bvps_val"].bfill().fillna(t_info.get("bvps", np.nan))
            grp = grp.drop(columns=["date_eff", "bvps_val"], errors="ignore")
        else: grp["bvps"] = t_info.get("bvps", np.nan)
        dfs.append(grp)

    df_clean = pd.concat(dfs, ignore_index=True)
    df_clean["eps_ttm"] = pd.to_numeric(df_clean["eps_ttm"], errors="coerce")
    df_clean["bvps"]    = pd.to_numeric(df_clean["bvps"], errors="coerce")
    df_clean["close"]   = pd.to_numeric(df_clean["close"], errors="coerce")

    df_clean["pe"] = np.where(df_clean["eps_ttm"] > 0, df_clean["close"] / df_clean["eps_ttm"], np.nan)
    df_clean["pb"] = np.where(df_clean["bvps"] > 0, df_clean["close"] / df_clean["bvps"], np.nan)

    is_vin = df_clean["group"] == VINGROUP_GROUP
    df_clean.loc[~is_vin & ((df_clean["pe"] < PE_MIN) | (df_clean["pe"] > PE_MAX)), "pe"] = np.nan
    df_clean.loc[~is_vin & ((df_clean["pb"] < PB_MIN) | (df_clean["pb"] > PB_MAX)), "pb"] = np.nan
    df_clean.loc[is_vin & (df_clean["pe"] < PE_MIN), "pe"] = np.nan

    df_clean.to_parquet(TICKER_HIST_FILE, index=False)
    sector_df = aggregate_snapshot(df_clean)
    sector_df.to_parquet(SECTOR_HIST_FILE, index=False)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Recompute 5-year historical P/E and P/B using Estimated Point-in-Time Fundamentals.
Applies regulatory disclosure cutoff rules (Circular 96/2020/TT-BTC) to historical
annual and quarterly financial statements so historical P/E & P/B dynamically track
earnings cycles across time.
"""

import json
import logging
import sys
import time
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
    """
    Given period string like '2023' (annual) or '2024-Q2' (quarterly),
    return estimated effective cutoff date YYYY-MM-DD when BCTC is published.
    """
    period_str = str(period_str).strip().upper()
    if "-Q" in period_str:
        parts = period_str.split("-Q")
        year = int(parts[0])
        q = int(parts[1])
        if q == 1:
            return f"{year}-05-01"
        elif q == 2:
            return f"{year}-08-01"
        elif q == 3:
            return f"{year}-11-01"
        elif q == 4:
            return f"{year + 1}-02-15"
    else:
        # Annual report for YYYY takes effect April 1 of YYYY+1
        try:
            year = int(period_str)
            return f"{year + 1}-04-01"
        except ValueError:
            pass
    return "1970-01-01"

def fetch_timeline_for_ticker(ticker: str, shares: float, fin) -> dict:
    """
    Fetch annual and quarterly income/balance sheets for ticker and build
    chronological effective timeline of eps_ttm and bvps.
    """
    eps_points = []
    bvps_points = []

    if not (pd.notna(shares) and shares > 0):
        return {"eps": [], "bvps": []}

    # 1. Annual Income Statement & Balance Sheet
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
                        if cutoff != "1970-01-01":
                            eps_points.append((cutoff, float(v / shares)))
    except Exception as exc:
        log.debug(f"  {ticker} annual income failed: {exc}")

    try:
        bs_yr = fin.balance_sheet(period="year", lang="en")
        if bs_yr is not None and not bs_yr.empty:
            meta_cols = {"item", "item_en", "item_id", "period"}
            val_cols = [c for c in bs_yr.columns if c not in meta_cols]
            eq = bs_yr[bs_yr["item_id"].astype(str).str.lower() == "bsa53"]
            if not eq.empty:
                for col in val_cols:
                    v = pd.to_numeric(eq[col].iloc[0], errors="coerce")
                    if pd.notna(v) and v != 0:
                        cutoff = get_cutoff_date_for_period(col)
                        if cutoff != "1970-01-01":
                            bvps_points.append((cutoff, float(v / shares)))
    except Exception as exc:
        log.debug(f"  {ticker} annual balance sheet failed: {exc}")

    # 2. Quarterly Income Statement & Balance Sheet
    try:
        is_q = fin.income_statement(period="quarter", lang="en")
        if is_q is not None and not is_q.empty:
            meta_cols = {"item", "item_en", "item_id", "period"}
            val_cols = [c for c in is_q.columns if c not in meta_cols]
            # Sort columns chronologically oldest -> newest for rolling 4-quarter sum
            val_cols_sorted = sorted(val_cols)
            pat = is_q[is_q["item_id"].astype(str).str.lower() == "isa22"]
            if not pat.empty and len(val_cols_sorted) >= 4:
                for i in range(3, len(val_cols_sorted)):
                    win = val_cols_sorted[i-3:i+1]
                    vals = pd.to_numeric(pat[win].iloc[0], errors="coerce")
                    if vals.notna().sum() == 4:
                        ttm_profit = float(vals.sum())
                        cutoff = get_cutoff_date_for_period(val_cols_sorted[i])
                        if cutoff != "1970-01-01":
                            eps_points.append((cutoff, float(ttm_profit / shares)))
    except Exception as exc:
        log.debug(f"  {ticker} quarterly income failed: {exc}")

    try:
        bs_q = fin.balance_sheet(period="quarter", lang="en")
        if bs_q is not None and not bs_q.empty:
            meta_cols = {"item", "item_en", "item_id", "period"}
            val_cols = [c for c in bs_q.columns if c not in meta_cols]
            eq = bs_q[bs_q["item_id"].astype(str).str.lower() == "bsa53"]
            if not eq.empty:
                for col in val_cols:
                    v = pd.to_numeric(eq[col].iloc[0], errors="coerce")
                    if pd.notna(v) and v != 0:
                        cutoff = get_cutoff_date_for_period(col)
                        if cutoff != "1970-01-01":
                            bvps_points.append((cutoff, float(v / shares)))
    except Exception as exc:
        log.debug(f"  {ticker} quarterly balance sheet failed: {exc}")

    # Sort timelines chronologically and deduplicate by date (keeping newest/latest calculation if tie)
    eps_dict = {}
    for dt, val in sorted(eps_points):
        eps_dict[dt] = val
    bvps_dict = {}
    for dt, val in sorted(bvps_points):
        bvps_dict[dt] = val

    return {
        "eps": sorted([list(item) for item in eps_dict.items()]),
        "bvps": sorted([list(item) for item in bvps_dict.items()])
    }

def main():
    log.info("Starting Estimated Point-in-Time Historical Fundamentals backfill...")
    if not Path(TICKER_HIST_FILE).exists() or not Path(FUND_FILE).exists():
        log.error("TICKER_HIST_FILE or FUND_FILE not found. Cannot proceed.")
        sys.exit(1)

    tick = pd.read_parquet(TICKER_HIST_FILE)
    fund = pd.read_parquet(FUND_FILE)
    if "ticker" not in fund.columns:
        fund = fund.reset_index()

    fund_map = fund.set_index("ticker").to_dict("index")
    unique_tickers = sorted(tick["ticker"].unique())
    log.info(f"Total unique tickers to process: {len(unique_tickers)}")

    cache = {}
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            log.info(f"Loaded {len(cache)} tickers from point_in_time_cache.json")
        except Exception as exc:
            log.warning(f"Could not load cache: {exc}")

    from vnstock import Finance
    tickers_needed = [t for t in unique_tickers if t not in cache]
    log.info(f"Need to fetch timelines for {len(tickers_needed)} tickers...")

    for idx, ticker in enumerate(tickers_needed, 1):
        if idx % 20 == 0 or idx == 1 or idx == len(tickers_needed):
            log.info(f"Fetching timeline [{idx}/{len(tickers_needed)}]: {ticker}")
        shares = fund_map.get(ticker, {}).get("shares", np.nan)
        try:
            fin = Finance(symbol=ticker, source="VCI")
            timeline = fetch_timeline_for_ticker(ticker, shares, fin)
            cache[ticker] = timeline
        except Exception as exc:
            log.debug(f"Error fetching timeline for {ticker}: {exc}")
            cache[ticker] = {"eps": [], "bvps": []}

        if idx % 15 == 0 or idx == len(tickers_needed):
            try:
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=2)
            except Exception as e:
                log.warning(f"Failed to write cache: {e}")
        time.sleep(0.12)

    log.info("All timelines fetched/loaded. Recomputing point-in-time P/E & P/B for all daily price records...")

    # Build point-in-time lookup and merge onto tick DataFrame per ticker
    tick["date"] = pd.to_datetime(tick["date"])
    tick = tick.sort_values(["ticker", "date"]).reset_index(drop=True)

    # Prepare columns
    if "shares" not in tick.columns:
        tick["shares"] = np.nan
    for col in ["eps_ttm", "bvps", "pe", "pb"]:
        tick[col] = np.nan

    dfs = []
    for ticker, grp in tick.groupby("ticker", sort=False):
        grp = grp.sort_values("date").copy()
        t_info = fund_map.get(ticker, {})
        static_shares = t_info.get("shares", np.nan)
        static_eps = t_info.get("eps_ttm", np.nan)
        static_bvps = t_info.get("bvps", np.nan)

        if pd.isna(grp["shares"].iloc[0]) and pd.notna(static_shares):
            grp["shares"] = static_shares

        t_cache = cache.get(ticker, {"eps": [], "bvps": []})
        eps_list = t_cache.get("eps", [])
        bvps_list = t_cache.get("bvps", [])

        if eps_list:
            eps_df = pd.DataFrame(eps_list, columns=["date_eff", "eps_val"])
            eps_df["date_eff"] = pd.to_datetime(eps_df["date_eff"])
            grp = pd.merge_asof(grp, eps_df.sort_values("date_eff"), left_on="date", right_on="date_eff", direction="backward")
            grp["eps_ttm"] = grp["eps_val"]
            grp = grp.drop(columns=["date_eff", "eps_val"], errors="ignore")
            # If dates before first cutoff exist, bfill with first available point-in-time eps or static fallback
            grp["eps_ttm"] = grp["eps_ttm"].bfill().fillna(static_eps)
        else:
            grp["eps_ttm"] = static_eps

        if bvps_list:
            bvps_df = pd.DataFrame(bvps_list, columns=["date_eff", "bvps_val"])
            bvps_df["date_eff"] = pd.to_datetime(bvps_df["date_eff"])
            grp = pd.merge_asof(grp, bvps_df.sort_values("date_eff"), left_on="date", right_on="date_eff", direction="backward")
            grp["bvps"] = grp["bvps_val"]
            grp = grp.drop(columns=["date_eff", "bvps_val"], errors="ignore")
            grp["bvps"] = grp["bvps"].bfill().fillna(static_bvps)
        else:
            grp["bvps"] = static_bvps

        dfs.append(grp)

    df_clean = pd.concat(dfs, ignore_index=True)
    df_clean["eps_ttm"] = pd.to_numeric(df_clean["eps_ttm"], errors="coerce")
    df_clean["bvps"] = pd.to_numeric(df_clean["bvps"], errors="coerce")
    df_clean["close"] = pd.to_numeric(df_clean["close"], errors="coerce")

    # Normalize close price to full VND if VCI returned prices in thousands (e.g. 10.21 -> 10210.0)
    df_clean["close"] = np.where((df_clean["close"] > 0) & (df_clean["close"] < 1000), df_clean["close"] * 1000, df_clean["close"])

    # Recompute PE / PB
    df_clean["pe"] = np.where(df_clean["eps_ttm"] > 0, df_clean["close"] / df_clean["eps_ttm"], np.nan)
    df_clean["pb"] = np.where(df_clean["bvps"] > 0, df_clean["close"] / df_clean["bvps"], np.nan)

    # Apply outlier filters (exempt Vingroup Ecosystem from PE_MAX/PB_MAX upper limits)
    is_vin = df_clean["group"] == VINGROUP_GROUP
    df_clean.loc[~is_vin & ((df_clean["pe"] < PE_MIN) | (df_clean["pe"] > PE_MAX)), "pe"] = np.nan
    df_clean.loc[~is_vin & ((df_clean["pb"] < PB_MIN) | (df_clean["pb"] > PB_MAX)), "pb"] = np.nan
    df_clean.loc[is_vin & (df_clean["pe"] < PE_MIN), "pe"] = np.nan

    log.info(f"Saving updated point-in-time ticker history to {TICKER_HIST_FILE}...")
    df_clean.to_parquet(TICKER_HIST_FILE, index=False)

    log.info("Aggregating snapshot for sector history...")
    sector_df = aggregate_snapshot(df_clean)
    log.info(f"Saving updated sector history to {SECTOR_HIST_FILE} (shape {sector_df.shape})...")
    sector_df.to_parquet(SECTOR_HIST_FILE, index=False)

    log.info("Recomputing complete. Run build_dashboard.py next to update dashboard and HTML.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Backfill 5-year historical daily close prices (Vnstock 4.0 compatible).
"""
import sys, logging, time
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from vnstock import Vnstock
from config import (
    FUND_FILE, TICKER_HIST_FILE, SECTOR_HIST_FILE,
    PE_MIN, PE_MAX, PB_MIN, PB_MAX,
    VINGROUP_TICKERS, VINGROUP_GROUP
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

def fetch_ticker_history(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    for attempt in range(15):
        try:
            stock = Vnstock().stock(symbol=ticker, source='VCI')
            df = stock.quote.history(start=start_date, end=end_date)
            if df is not None and not df.empty and "time" in df.columns and "close" in df.columns:
                df["ticker"] = ticker
                df["date"] = pd.to_datetime(df["time"]).dt.date
                time.sleep(2.15)
                return df[["date", "ticker", "close"]].copy()
            time.sleep(0.5)
            return pd.DataFrame()
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt): raise
            err_str = str(e).lower()
            if "rate" in err_str or "limit" in err_str or "429" in err_str:
                log.warning(f"Rate limit hit for {ticker}, sleeping 62s (attempt {attempt+1}/15)...")
                time.sleep(62)
            else:
                time.sleep(1)
    return pd.DataFrame()

# ( aggregate_snapshot() giữ nguyên như cũ )
def aggregate_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if "shares" not in df.columns: df["shares"] = np.nan
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0)

    for dt, dt_df in df.groupby("date"):
        pe = dt_df["pe"].dropna(); pb = dt_df["pb"].dropna()
        pe_valid = dt_df[dt_df["pe"].notna() & (dt_df["shares"] > 0)]
        sum_pe_mc  = (pe_valid["close"] * pe_valid["shares"]).sum()
        sum_pe_ern = (pe_valid["eps_ttm"] * pe_valid["shares"]).sum()
        w_pe = sum_pe_mc / sum_pe_ern if len(pe_valid) > 0 and sum_pe_ern > 0 else np.nan

        pb_valid = dt_df[dt_df["pb"].notna() & (dt_df["shares"] > 0)]
        sum_pb_mc = (pb_valid["close"] * pb_valid["shares"]).sum()
        sum_pb_bv = (pb_valid["bvps"] * pb_valid["shares"]).sum()
        w_pb = sum_pb_mc / sum_pb_bv if len(pb_valid) > 0 and sum_pb_bv > 0 else np.nan

        rows.append({"date": dt, "group": "VN-Index", "count": len(dt_df),
                     "valid_pe": len(pe), "valid_pb": len(pb),
                     "median_pe": pe.median() if len(pe) else np.nan, "median_pb": pb.median() if len(pb) else np.nan,
                     "mean_pe": pe.mean() if len(pe) else np.nan, "mean_pb": pb.mean() if len(pb) else np.nan,
                     "weighted_pe": w_pe, "weighted_pb": w_pb,
                     "p25_pe": pe.quantile(.25) if len(pe) else np.nan, "p75_pe": pe.quantile(.75) if len(pe) else np.nan,
                     "p25_pb": pb.quantile(.25) if len(pb) else np.nan, "p75_pb": pb.quantile(.75) if len(pb) else np.nan})

    for (dt, grp_name), grp in df.groupby(["date", "group"]):
        pe = grp["pe"].dropna(); pb = grp["pb"].dropna()
        pe_valid = grp[grp["pe"].notna() & (grp["shares"] > 0)]
        sum_pe_mc  = (pe_valid["close"] * pe_valid["shares"]).sum()
        sum_pe_ern = (pe_valid["eps_ttm"] * pe_valid["shares"]).sum()
        w_pe = sum_pe_mc / sum_pe_ern if len(pe_valid) > 0 and sum_pe_ern > 0 else np.nan

        pb_valid = grp[grp["pb"].notna() & (grp["shares"] > 0)]
        sum_pb_mc = (pb_valid["close"] * pb_valid["shares"]).sum()
        sum_pb_bv = (pb_valid["bvps"] * pb_valid["shares"]).sum()
        w_pb = sum_pb_mc / sum_pb_bv if len(pb_valid) > 0 and sum_pb_bv > 0 else np.nan

        rows.append({"date": dt, "group": grp_name, "count": len(grp),
                     "valid_pe": len(pe), "valid_pb": len(pb),
                     "median_pe": pe.median() if len(pe) else np.nan, "median_pb": pb.median() if len(pb) else np.nan,
                     "mean_pe": pe.mean() if len(pe) else np.nan, "mean_pb": pb.mean() if len(pb) else np.nan,
                     "weighted_pe": w_pe, "weighted_pb": w_pb,
                     "p25_pe": pe.quantile(.25) if len(pe) else np.nan, "p75_pe": pe.quantile(.75) if len(pe) else np.nan,
                     "p25_pb": pb.quantile(.25) if len(pb) else np.nan, "p75_pb": pb.quantile(.75) if len(pb) else np.nan})
    return pd.DataFrame(rows).sort_values(["date", "group"]).reset_index(drop=True)

def main():
    if not Path(FUND_FILE).exists():
        log.error(f"Fundamentals file {FUND_FILE} missing!")
        sys.exit(1)

    fund = pd.read_parquet(FUND_FILE)
    if "ticker" not in fund.columns: fund = fund.reset_index()
    tickers = sorted([str(t).strip() for t in fund["ticker"].unique() if len(str(t).strip()) == 3])

    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=365 * 5 + 10)).strftime("%Y-%m-%d")

    history_dfs = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_to_ticker = {executor.submit(fetch_ticker_history, t, start_date, end_date): t for t in tickers}
        for i, fut in enumerate(future_to_ticker, 1):
            try:
                res = fut.result()
                if not res.empty: history_dfs.append(res)
            except BaseException as e:
                if isinstance(e, KeyboardInterrupt): raise

    if not history_dfs:
        log.error("No historical data fetched!")
        sys.exit(1)

    all_prices = pd.concat(history_dfs, ignore_index=True)
    fund_cols = ["ticker", "eps_ttm", "bvps", "sector", "industry", "group"]
    if "shares" in fund.columns: fund_cols.append("shares")
    df = all_prices.merge(fund[fund_cols], on="ticker", how="left")

    for col in ("sector", "industry", "group"): df[col] = df[col].fillna("Unknown")
    mask = df["ticker"].isin(VINGROUP_TICKERS)
    df.loc[mask, "group"] = VINGROUP_GROUP

    df["eps_ttm"] = pd.to_numeric(df["eps_ttm"], errors="coerce")
    df["bvps"]    = pd.to_numeric(df["bvps"], errors="coerce")
    df["close"]   = pd.to_numeric(df["close"], errors="coerce")

    # Vnstock 4.0 returns full VND, no need to scale * 1000
    df["pe"] = np.where(df["eps_ttm"] > 0, df["close"] / df["eps_ttm"], np.nan)
    df["pb"] = np.where(df["bvps"] > 0, df["close"] / df["bvps"], np.nan)

    is_vin = df["group"] == VINGROUP_GROUP
    df.loc[~is_vin & ((df["pe"] < PE_MIN) | (df["pe"] > PE_MAX)), "pe"] = np.nan
    df.loc[~is_vin & ((df["pb"] < PB_MIN) | (df["pb"] > PB_MAX)), "pb"] = np.nan
    df.loc[is_vin & (df["pe"] < PE_MIN), "pe"] = np.nan
    df.loc[is_vin & (df["pb"] < PB_MIN), "pb"] = np.nan

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.drop_duplicates(subset=["date", "ticker"], keep="last").reset_index(drop=True)

    sector_agg = aggregate_snapshot(df)

    save_cols = ["date", "ticker", "close", "pe", "pb", "sector", "industry", "group"]
    if "shares" in df.columns: save_cols.append("shares")
    df_save = df[save_cols].copy()
    df_save["date"] = pd.to_datetime(df_save["date"])
    df_save.to_parquet(TICKER_HIST_FILE, index=False)

    sector_agg["date"] = pd.to_datetime(sector_agg["date"])
    sector_agg.to_parquet(SECTOR_HIST_FILE, index=False)

if __name__ == "__main__":
    main()

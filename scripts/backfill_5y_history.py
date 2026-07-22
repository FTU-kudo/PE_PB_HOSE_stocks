#!/usr/bin/env python3
"""
Backfill 5-year historical daily close prices from VNStock, combine with
current fundamentals (EPS/BVPS) to compute historical P/E and P/B medians
for every trading date and group across the past 5 years.
Saves/updates data/ticker_history.parquet and data/sector_history.parquet.
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from vnstock.api.quote import Quote
from config import (
    FUND_FILE, TICKER_HIST_FILE, SECTOR_HIST_FILE,
    PE_MIN, PE_MAX, PB_MIN, PB_MAX,
    VINGROUP_TICKERS, VINGROUP_GROUP
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)


def fetch_ticker_history(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    import time
    for attempt in range(15):
        try:
            q = Quote(symbol=ticker, source='VCI')
            df = q.history(start=start_date, end=end_date)
            if df is not None and not df.empty and "time" in df.columns and "close" in df.columns:
                df["ticker"] = ticker
                df["date"] = pd.to_datetime(df["time"]).dt.date
                time.sleep(2.15)
                return df[["date", "ticker", "close"]].copy()
            time.sleep(0.5)
            return pd.DataFrame()
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                raise
            err_str = str(e).lower()
            if isinstance(e, SystemExit) or "rate" in err_str or "limit" in err_str or "429" in err_str or "giới hạn" in err_str:
                log.warning(f"Rate limit / SystemExit hit for {ticker}, sleeping 62s to reset 1-minute window (attempt {attempt+1}/15)...")
                time.sleep(62)
            else:
                log.debug(f"Failed VCI history for {ticker}: {e}")
                time.sleep(1)
    return pd.DataFrame()


def aggregate_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (dt, grp_name), grp in df.groupby(["date", "group"]):
        pe = grp["pe"].dropna()
        pb = grp["pb"].dropna()
        rows.append({
            "date":      dt,
            "group":     grp_name,
            "count":     len(grp),
            "valid_pe":  len(pe),
            "valid_pb":  len(pb),
            "median_pe": pe.median()      if len(pe) else np.nan,
            "median_pb": pb.median()      if len(pb) else np.nan,
            "mean_pe":   pe.mean()        if len(pe) else np.nan,
            "mean_pb":   pb.mean()        if len(pb) else np.nan,
            "p25_pe":    pe.quantile(.25) if len(pe) else np.nan,
            "p75_pe":    pe.quantile(.75) if len(pe) else np.nan,
            "p25_pb":    pb.quantile(.25) if len(pb) else np.nan,
            "p75_pb":    pb.quantile(.75) if len(pb) else np.nan,
        })
    return pd.DataFrame(rows).sort_values(["date", "group"]).reset_index(drop=True)


def main():
    if not Path(FUND_FILE).exists():
        log.error(f"Fundamentals file {FUND_FILE} missing!")
        sys.exit(1)

    fund = pd.read_parquet(FUND_FILE)
    tickers = sorted([str(t).strip() for t in fund["ticker"].unique() if len(str(t).strip()) == 3])
    log.info(f"Loaded {len(tickers)} valid 3-letter tickers from {FUND_FILE}.")

    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=365 * 5 + 10)).strftime("%Y-%m-%d")

    checkpoint_file = Path("data/ticker_history_checkpoint.parquet")
    history_dfs = []
    fetched_tickers = set()

    if checkpoint_file.exists():
        try:
            chk_df = pd.read_parquet(checkpoint_file)
            if not chk_df.empty and "ticker" in chk_df.columns:
                counts = chk_df.groupby("ticker").size()
                valid_chk_tickers = set(counts[counts > 100].index)
                if valid_chk_tickers:
                    history_dfs.append(chk_df[chk_df["ticker"].isin(valid_chk_tickers)].copy())
                    fetched_tickers = valid_chk_tickers
                    log.info(f"Loaded checkpoint with {len(fetched_tickers)} already fetched tickers (>100 records each).")
        except Exception as e:
            log.warning(f"Could not read checkpoint file: {e}")

    remaining_tickers = [t for t in tickers if t not in fetched_tickers]
    log.info(f"Fetching 5-year daily history from {start_date} to {end_date} across {len(remaining_tickers)} remaining tickers using 2 workers (sleeping 2.15s between requests)...")

    if remaining_tickers:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_to_ticker = {executor.submit(fetch_ticker_history, t, start_date, end_date): t for t in remaining_tickers}
            for i, fut in enumerate(future_to_ticker, 1):
                try:
                    res = fut.result()
                    if not res.empty:
                        history_dfs.append(res)
                except BaseException as e:
                    if isinstance(e, KeyboardInterrupt):
                        raise
                    log.error(f"Error fetching {future_to_ticker[fut]}: {e}")

                if i % 15 == 0 or i == len(remaining_tickers):
                    log.info(f"Progress: {i}/{len(remaining_tickers)} remaining tickers fetched...")
                    # Save checkpoint
                    if history_dfs:
                        try:
                            chk_save = pd.concat(history_dfs, ignore_index=True)
                            chk_save.to_parquet(checkpoint_file, index=False)
                        except Exception as ce:
                            log.debug(f"Failed checkpoint save: {ce}")

    if not history_dfs:
        log.error("No historical data fetched!")
        sys.exit(1)

    all_prices = pd.concat(history_dfs, ignore_index=True)
    log.info(f"Fetched {len(all_prices)} daily price records across {all_prices['ticker'].nunique()} tickers.")

    # Merge fundamentals
    fund_sub = fund[["ticker", "eps_annual", "bvps", "sector", "industry", "group"]].copy()
    df = all_prices.merge(fund_sub, on="ticker", how="left")

    df["sector"]   = df["sector"].fillna("Unknown")
    df["industry"] = df["industry"].fillna("Unknown")
    df["group"]    = df["group"].fillna("Unknown")

    mask = df["ticker"].isin(VINGROUP_TICKERS)
    df.loc[mask, "group"] = VINGROUP_GROUP

    df["eps_annual"] = pd.to_numeric(df["eps_annual"], errors="coerce")
    df["bvps"]       = pd.to_numeric(df["bvps"],       errors="coerce")
    df["close"]      = pd.to_numeric(df["close"],      errors="coerce")

    # Normalize close price to full VND if VCI returned prices in thousands (e.g. 10.21 -> 10210.0)
    df["close"] = np.where((df["close"] > 0) & (df["close"] < 1000), df["close"] * 1000, df["close"])

    # Compute PE / PB
    df["pe"] = np.where(df["eps_annual"] > 0, df["close"] / df["eps_annual"], np.nan)
    df["pb"] = np.where(df["bvps"] > 0, df["close"] / df["bvps"], np.nan)

    # Windsorise / outlier filter
    df.loc[(df["pe"] < PE_MIN) | (df["pe"] > PE_MAX), "pe"] = np.nan
    df.loc[(df["pb"] < PB_MIN) | (df["pb"] > PB_MAX), "pb"] = np.nan

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.drop_duplicates(subset=["date", "ticker"], keep="last").reset_index(drop=True)

    log.info("Aggregating 5-year sector history...")
    sector_agg = aggregate_snapshot(df)
    log.info(f"Aggregated {len(sector_agg)} sector-date rows across {sector_agg['date'].nunique()} trading dates.")

    # Save ticker history
    df_save = df[["date", "ticker", "close", "pe", "pb", "sector", "industry", "group"]]
    df_save["date"] = pd.to_datetime(df_save["date"])
    df_save.to_parquet(TICKER_HIST_FILE, index=False)
    log.info(f"Saved {len(df_save)} rows -> {TICKER_HIST_FILE}")

    # Save sector history
    sector_agg["date"] = pd.to_datetime(sector_agg["date"])
    sector_agg.to_parquet(SECTOR_HIST_FILE, index=False)
    log.info(f"Saved {len(sector_agg)} rows -> {SECTOR_HIST_FILE}")

    if checkpoint_file.exists():
        try:
            checkpoint_file.unlink()
            log.info(f"Cleaned up checkpoint file {checkpoint_file}")
        except Exception as ce:
            log.debug(f"Could not remove checkpoint: {ce}")


if __name__ == "__main__":
    main()

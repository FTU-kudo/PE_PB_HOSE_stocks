#!/usr/bin/env python3
"""
Clean historical close prices (converting VCI prices in thousands of VND to full VND)
and recompute P/E & P/B across all 5 years of historical data for every company and group/sector.
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scripts.config import (
    TICKER_HIST_FILE, SECTOR_HIST_FILE, FUND_FILE,
    PE_MIN, PE_MAX, PB_MIN, PB_MAX,
    VINGROUP_TICKERS, VINGROUP_GROUP
)

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
    print("Loading ticker history and fundamentals...")
    tick = pd.read_parquet(TICKER_HIST_FILE)
    fund = pd.read_parquet(FUND_FILE)[["ticker", "eps_annual", "bvps", "sector", "industry", "group"]]

    print(f"Loaded {len(tick)} daily records. Normalizing close prices to full VND...")
    tick["close"] = pd.to_numeric(tick["close"], errors="coerce")
    tick["close"] = np.where((tick["close"] > 0) & (tick["close"] < 1000), tick["close"] * 1000, tick["close"])

    # Merge with latest fundamentals to ensure clean grouping and calculation
    df = tick[["date", "ticker", "close"]].merge(fund, on="ticker", how="left")
    df["sector"]   = df["sector"].fillna("Unknown")
    df["industry"] = df["industry"].fillna("Unknown")
    df["group"]    = df["group"].fillna("Unknown")

    mask = df["ticker"].isin(VINGROUP_TICKERS)
    df.loc[mask, "group"] = VINGROUP_GROUP

    df["eps_annual"] = pd.to_numeric(df["eps_annual"], errors="coerce")
    df["bvps"]       = pd.to_numeric(df["bvps"],       errors="coerce")

    print("Recomputing P/E and P/B ratios...")
    df["pe"] = np.where(df["eps_annual"] > 0, df["close"] / df["eps_annual"], np.nan)
    df["pb"] = np.where(df["bvps"] > 0, df["close"] / df["bvps"], np.nan)

    print("Applying guard-rails (filtering extreme outliers)...")
    df.loc[(df["pe"] < PE_MIN) | (df["pe"] > PE_MAX), "pe"] = np.nan
    df.loc[(df["pb"] < PB_MIN) | (df["pb"] > PB_MAX), "pb"] = np.nan

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.drop_duplicates(subset=["date", "ticker"], keep="last").reset_index(drop=True)

    pe_valid = df["pe"].notna().sum()
    pb_valid = df["pb"].notna().sum()
    print(f"Recomputed ticker history | valid PE: {pe_valid}/{len(df)} ({100*pe_valid/len(df):.1f}%) | valid PB: {pb_valid}/{len(df)} ({100*pb_valid/len(df):.1f}%)")

    # Save cleaned ticker history
    df_save = df[["date", "ticker", "close", "pe", "pb", "sector", "industry", "group"]].copy()
    df_save["date"] = pd.to_datetime(df_save["date"])
    df_save.to_parquet(TICKER_HIST_FILE, index=False)
    print(f"Saved cleaned ticker history -> {TICKER_HIST_FILE}")

    print("Aggregating sector history across all dates and groups...")
    sector_agg = aggregate_snapshot(df)
    sector_agg["date"] = pd.to_datetime(sector_agg["date"])
    sector_agg.to_parquet(SECTOR_HIST_FILE, index=False)
    print(f"Saved recomputed sector history -> {SECTOR_HIST_FILE} ({len(sector_agg)} rows across {sector_agg['date'].nunique()} trading dates)")

if __name__ == "__main__":
    main()

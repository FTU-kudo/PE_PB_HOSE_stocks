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
    if "shares" not in df.columns:
        df["shares"] = np.nan
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0)

    # 1. First, aggregate across all rows per date -> "VN-Index"
    for dt, dt_df in df.groupby("date"):
        pe = dt_df["pe"].dropna()
        pb = dt_df["pb"].dropna()

        pe_valid = dt_df[dt_df["pe"].notna() & (dt_df["shares"] > 0)]
        sum_pe_mc  = (pe_valid["close"] * pe_valid["shares"]).sum()
        sum_pe_ern = (pe_valid["eps_annual"] * pe_valid["shares"]).sum()
        w_pe = sum_pe_mc / sum_pe_ern if len(pe_valid) > 0 and sum_pe_ern > 0 else np.nan

        pb_valid = dt_df[dt_df["pb"].notna() & (dt_df["shares"] > 0)]
        sum_pb_mc = (pb_valid["close"] * pb_valid["shares"]).sum()
        sum_pb_bv = (pb_valid["bvps"] * pb_valid["shares"]).sum()
        w_pb = sum_pb_mc / sum_pb_bv if len(pb_valid) > 0 and sum_pb_bv > 0 else np.nan

        rows.append({
            "date":        dt,
            "group":       "VN-Index",
            "count":       len(dt_df),
            "valid_pe":    len(pe),
            "valid_pb":    len(pb),
            "median_pe":   pe.median()      if len(pe) else np.nan,
            "median_pb":   pb.median()      if len(pb) else np.nan,
            "mean_pe":     pe.mean()        if len(pe) else np.nan,
            "mean_pb":     pb.mean()        if len(pb) else np.nan,
            "weighted_pe": w_pe,
            "weighted_pb": w_pb,
            "p25_pe":      pe.quantile(.25) if len(pe) else np.nan,
            "p75_pe":      pe.quantile(.75) if len(pe) else np.nan,
            "p25_pb":      pb.quantile(.25) if len(pb) else np.nan,
            "p75_pb":      pb.quantile(.75) if len(pb) else np.nan,
        })

    # 2. Second, aggregate per group
    for (dt, grp_name), grp in df.groupby(["date", "group"]):
        pe = grp["pe"].dropna()
        pb = grp["pb"].dropna()

        pe_valid = grp[grp["pe"].notna() & (grp["shares"] > 0)]
        sum_pe_mc  = (pe_valid["close"] * pe_valid["shares"]).sum()
        sum_pe_ern = (pe_valid["eps_annual"] * pe_valid["shares"]).sum()
        w_pe = sum_pe_mc / sum_pe_ern if len(pe_valid) > 0 and sum_pe_ern > 0 else np.nan

        pb_valid = grp[grp["pb"].notna() & (grp["shares"] > 0)]
        sum_pb_mc = (pb_valid["close"] * pb_valid["shares"]).sum()
        sum_pb_bv = (pb_valid["bvps"] * pb_valid["shares"]).sum()
        w_pb = sum_pb_mc / sum_pb_bv if len(pb_valid) > 0 and sum_pb_bv > 0 else np.nan

        rows.append({
            "date":        dt,
            "group":       grp_name,
            "count":       len(grp),
            "valid_pe":    len(pe),
            "valid_pb":    len(pb),
            "median_pe":   pe.median()      if len(pe) else np.nan,
            "median_pb":   pb.median()      if len(pb) else np.nan,
            "mean_pe":     pe.mean()        if len(pe) else np.nan,
            "mean_pb":     pb.mean()        if len(pb) else np.nan,
            "weighted_pe": w_pe,
            "weighted_pb": w_pb,
            "p25_pe":      pe.quantile(.25) if len(pe) else np.nan,
            "p75_pe":      pe.quantile(.75) if len(pe) else np.nan,
            "p25_pb":      pb.quantile(.25) if len(pb) else np.nan,
            "p75_pb":      pb.quantile(.75) if len(pb) else np.nan,
        })
    return pd.DataFrame(rows).sort_values(["date", "group"]).reset_index(drop=True)

def main():
    print("Loading ticker history and fundamentals...")
    tick = pd.read_parquet(TICKER_HIST_FILE)
    fund_full = pd.read_parquet(FUND_FILE)
    if "ticker" not in fund_full.columns:
        fund_full = fund_full.reset_index()
    fund_full["group"] = fund_full["sector"]

    mask_bds_fund = fund_full["industry"].astype(str).str.lower().str.contains("bất động|real estate")
    fund_full.loc[mask_bds_fund, "sector"] = "Bất động sản"
    fund_full.loc[mask_bds_fund, "group"]  = "Bất động sản"

    mask_xd_fund = fund_full["industry"].astype(str).str.lower().str.contains("xây dựng và vật liệu|construction & materials|construction and materials")
    fund_full.loc[mask_xd_fund, "sector"] = "Xây dựng và Vật liệu"
    fund_full.loc[mask_xd_fund, "group"]  = "Xây dựng và Vật liệu"

    mask_hc_fund = fund_full["industry"].astype(str).str.lower().str.contains("hóa chất|chemical")
    fund_full.loc[mask_hc_fund, "sector"] = "Hóa chất"
    fund_full.loc[mask_hc_fund, "group"]  = "Hóa chất"

    mask_tp_fund = fund_full["industry"].astype(str).str.lower().str.contains("sản xuất thực phẩm|food producer")
    fund_full.loc[mask_tp_fund, "sector"] = "Sản xuất thực phẩm"
    fund_full.loc[mask_tp_fund, "group"]  = "Sản xuất thực phẩm"

    mask_vin_fund = fund_full["ticker"].isin(VINGROUP_TICKERS)
    fund_full.loc[mask_vin_fund, "group"] = VINGROUP_GROUP
    fund_full.to_parquet(FUND_FILE)

    fund_cols = ["ticker", "eps_annual", "bvps", "sector", "industry", "group"]
    if "shares" in fund_full.columns:
        fund_cols.append("shares")
    fund = fund_full[fund_cols]

    print(f"Loaded {len(tick)} daily records. Normalizing close prices to full VND...")
    tick["close"] = pd.to_numeric(tick["close"], errors="coerce")
    tick["close"] = np.where((tick["close"] > 0) & (tick["close"] < 1000), tick["close"] * 1000, tick["close"])

    # Merge with latest fundamentals to ensure clean grouping and calculation
    df = tick[["date", "ticker", "close"]].merge(fund, on="ticker", how="left")
    df["sector"]   = df["sector"].fillna("Unknown")
    df["industry"] = df["industry"].fillna("Unknown")
    df["group"]    = df["group"].fillna("Unknown")

    mask_bds = df["industry"].astype(str).str.lower().str.contains("bất động|real estate")
    df.loc[mask_bds, "sector"] = "Bất động sản"
    df.loc[mask_bds, "group"]  = "Bất động sản"

    mask_xd = df["industry"].astype(str).str.lower().str.contains("xây dựng và vật liệu|construction & materials|construction and materials")
    df.loc[mask_xd, "sector"] = "Xây dựng và Vật liệu"
    df.loc[mask_xd, "group"]  = "Xây dựng và Vật liệu"

    mask_hc = df["industry"].astype(str).str.lower().str.contains("hóa chất|chemical")
    df.loc[mask_hc, "sector"] = "Hóa chất"
    df.loc[mask_hc, "group"]  = "Hóa chất"

    mask_tp = df["industry"].astype(str).str.lower().str.contains("sản xuất thực phẩm|food producer")
    df.loc[mask_tp, "sector"] = "Sản xuất thực phẩm"
    df.loc[mask_tp, "group"]  = "Sản xuất thực phẩm"

    mask = df["ticker"].isin(VINGROUP_TICKERS)
    df.loc[mask, "group"] = VINGROUP_GROUP

    df["eps_annual"] = pd.to_numeric(df["eps_annual"], errors="coerce")
    df["bvps"]       = pd.to_numeric(df["bvps"],       errors="coerce")

    print("Recomputing P/E and P/B ratios...")
    df["pe"] = np.where(df["eps_annual"] > 0, df["close"] / df["eps_annual"], np.nan)
    df["pb"] = np.where(df["bvps"] > 0, df["close"] / df["bvps"], np.nan)

    print("Applying guard-rails (filtering extreme outliers, exempting Vingroup Ecosystem from PE_MAX/PB_MAX)...")
    is_vin = df["group"] == VINGROUP_GROUP
    df.loc[~is_vin & ((df["pe"] < PE_MIN) | (df["pe"] > PE_MAX)), "pe"] = np.nan
    df.loc[~is_vin & ((df["pb"] < PB_MIN) | (df["pb"] > PB_MAX)), "pb"] = np.nan
    df.loc[is_vin & (df["pe"] < PE_MIN), "pe"] = np.nan
    df.loc[is_vin & (df["pb"] < PB_MIN), "pb"] = np.nan

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.drop_duplicates(subset=["date", "ticker"], keep="last").reset_index(drop=True)

    pe_valid = df["pe"].notna().sum()
    pb_valid = df["pb"].notna().sum()
    print(f"Recomputed ticker history | valid PE: {pe_valid}/{len(df)} ({100*pe_valid/len(df):.1f}%) | valid PB: {pb_valid}/{len(df)} ({100*pb_valid/len(df):.1f}%)")

    # Save cleaned ticker history
    save_cols = ["date", "ticker", "close", "pe", "pb", "sector", "industry", "group"]
    if "shares" in df.columns:
        save_cols.append("shares")
    df_save = df[save_cols].copy()
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

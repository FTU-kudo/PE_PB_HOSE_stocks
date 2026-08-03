"""
Daily P/E & P/B pipeline  (runs weekdays ~16:05 ICT = 09:05 UTC).

Flow
----
1.  Load fundamentals cache  (data/fundamentals.parquet)
    ↳ Warn if stale (>8 days), but still proceed.
2.  Fetch today's close prices for all HOSE tickers
    via KBS price_board (batch = 50 tickers / call).
3.  Compute:
        PE_daily = close_price  / eps_ttm
        PB_daily = close_price  / bvps
    Both capped to [PE_MIN, PE_MAX] and [PB_MIN, PB_MAX].
4.  Sector aggregation (median, mean, IQR) per group.
5.  Append to ticker_history.parquet and sector_history.parquet.
6.  Save today's CSV snapshot for transparency.

Why TTM EPS?
  VAS quarterly income statements are year-to-date cumulative (Q2 IS = H1 P&L).
  Deaccumulating to period-specific EPS before TTM summation is complex and
  error-prone in an automated pipeline. Using the last audited TTM EPS is
  safer for market-level P/E analysis. (TTM can be added later via IS pipeline.)
"""

import os
import sys
import time
import logging
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Load .env file if present (contains VNSTOCK_API_KEY for higher rate limits)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, rely on system env vars

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import (
    VINGROUP_TICKERS, VINGROUP_GROUP,
    PE_MIN, PE_MAX, PB_MIN, PB_MAX,
    PRICE_BOARD_BATCH, FUND_STALE_DAYS,
    DATA_DIR, DAILY_DIR, FUND_FILE,
    TICKER_HIST_FILE, SECTOR_HIST_FILE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

for d in [DATA_DIR, DAILY_DIR, "docs"]:
    Path(d).mkdir(parents=True, exist_ok=True)


# ── Auth ──────────────────────────────────────────────────────────────────────
def register_vnstock() -> None:
    """
    Attempt to register the vnstock API key.
    Tries all known import paths across vnstock versions.
    Silently skips if key is not set or if registration is unavailable.
    """
    api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
    if not api_key:
        log.warning("VNSTOCK_API_KEY not set — running as Guest (20 req/min).")
        return

    registered = False
    attempts = [
        ("vnstock",             "register_user"),
        ("vnstock.common.user", "register_user"),
        ("vnstock.core.utils",  "register_user"),
        ("vnstock",             "init_user"),
    ]
    for module, func in attempts:
        try:
            mod = __import__(module, fromlist=[func])
            getattr(mod, func)(api_key=api_key)
            log.info(f"vnstock registered via {module}.{func}")
            registered = True
            break
        except (ImportError, AttributeError):
            continue
        except Exception as exc:
            log.warning(f"{module}.{func} raised: {exc}")
            break

    if not registered:
        log.warning("Could not register API key (function not found). Guest mode.")
# ── Load fundamentals ─────────────────────────────────────────────────────────
def load_fundamentals() -> pd.DataFrame:
    """
    Load the cached EPS / BVPS parquet file.
    Warn if stale; exit if missing (weekly job must run first).
    """
    fund_path = Path(FUND_FILE)
    if not fund_path.exists():
        log.error(
            f"{FUND_FILE} not found. "
            "Run scripts/fetch_fundamentals.py first (weekly workflow)."
        )
        sys.exit(1)

    mtime = date.fromtimestamp(fund_path.stat().st_mtime)
    age   = (date.today() - mtime).days
    if age > FUND_STALE_DAYS:
        log.warning(
            f"Fundamentals cache is {age} days old (threshold={FUND_STALE_DAYS}). "
            "Proceeding, but trigger the weekly workflow to refresh."
        )

    df = pd.read_parquet(fund_path)

    # Diagnostic: log columns and a small sample so CI output shows what we actually got
    log.info(f"Fundamentals columns: {list(df.columns)}")
    try:
        log.info(f"Fundamentals sample:\n{df.head(3).to_string(index=False)}")
    except Exception:
        # head().to_string may fail if weird types; ignore
        pass

    # --- Robust EPS / BVPS normalization (case-insensitive) ---
    cols_lower = {c.lower(): c for c in df.columns}

    # EPS candidates in order of preference
    eps_candidates = ["eps_ttm", "eps", "eps_annual", "eps_basic", "eps_diluted", "eps_basic_diluted"]
    for cand in eps_candidates:
        if cand in cols_lower:
            if cols_lower[cand] != "eps_ttm":
                df = df.rename(columns={cols_lower[cand]: "eps_ttm"})
                log.info(f"Renamed '{cols_lower[cand]}' -> 'eps_ttm'")
            break
    else:
        # no eps-like column found
        log.error(
            "Không tìm thấy cột EPS (các biến thử: eps_ttm, eps, eps_annual, eps_basic, eps_diluted). "
            "Inspect fundamentals.parquet (logged columns above)."
        )
        sys.exit(1)

    # BVPS candidates
    bvps_candidates = ["bvps", "book_value_per_share", "bookvaluepershare", "book_value"]
    for cand in bvps_candidates:
        if cand in cols_lower:
            if cols_lower[cand] != "bvps":
                df = df.rename(columns={cols_lower[cand]: "bvps"})
                log.info(f"Renamed '{cols_lower[cand]}' -> 'bvps'")
            break
    # bvps is optional in your pipeline; we don't exit if missing but we log
    if "bvps" not in df.columns:
        log.warning("BVPS column not found (no 'bvps' or 'book_value_per_share'). PB will be NaN for all tickers.")

    # -----------------------------------------------------------------------

    if "ticker" not in df.columns:
        df = df.reset_index()
    df["ticker"] = df["ticker"].str.upper()
    df = df[df["ticker"].astype(str).str.len() == 3]
    df = df.drop_duplicates(subset=["ticker"], keep="first")
    log.info(f"Fundamentals loaded: {len(df)} tickers  (cache age: {age} days)")
    return df.set_index("ticker")


# ── Compute PE / PB ───────────────────────────────────────────────────────────
def compute_pe_pb(close: pd.Series, fundamentals: pd.DataFrame) -> pd.DataFrame:
    """
    Merge daily close with cached EPS / BVPS / shares and compute PE / PB.

    PE = close / eps_ttm
    PB = close / bvps

    Both are windsorised to [PE_MIN, PE_MAX] and [PB_MIN, PB_MAX]:
    values outside the range become NaN (not capped) so they do not
    distort sector medians.
    """
    df = close.rename("close").reset_index()
    df.columns = ["ticker", "close"]

    # Defensive handling: ensure fundamentals has ticker as column and expected cols
    fund = fundamentals.copy()
    # If ticker is the index, bring it back as a column for a clean merge
    if fund.index.name == "ticker" or "ticker" not in fund.columns:
        fund = fund.reset_index()

    expected = ["eps_ttm", "bvps", "sector", "industry", "group"]
    # create missing expected columns with NaN instead of letting selection raise KeyError
    for c in expected:
        if c not in fund.columns:
            fund[c] = np.nan

    # keep shares if present
    keep_cols = expected + (["shares"] if "shares" in fund.columns else [])
    fund = fund[["ticker"] + [c for c in keep_cols if c != "ticker"]].copy()

    df = df.merge(fund, on="ticker", how="left")

    # Fill missing group for non-fundamentals tickers
    df["sector"]   = df["sector"].fillna("Unknown")
    df["industry"] = df["industry"].fillna("Unknown")
    df["group"]    = df["group"].fillna("Unknown")

    # Sector mapping (same logic as before; keeps safe .astype(str))
    mask_bds = df["industry"].astype(str).str.lower().str.contains("bất động|real estate", na=False)
    df.loc[mask_bds, ["sector", "group"]] = "Bất động sản"
    mask_xd = df["industry"].astype(str).str.lower().str.contains("xây dựng và vật liệu|construction & materials|construction and materials", na=False)
    df.loc[mask_xd, ["sector", "group"]] = "Xây dựng và Vật liệu"
    mask_hc = df["industry"].astype(str).str.lower().str.contains("hóa chất|chemical", na=False)
    df.loc[mask_hc, ["sector", "group"]] = "Hóa chất"
    mask_tp = df["industry"].astype(str).str.lower().str.contains("sản xuất thực phẩm|food producer", na=False)
    df.loc[mask_tp, ["sector", "group"]] = "Sản xuất thực phẩm"

    # Vingroup override (in case sector map was stale)
    df.loc[df["ticker"].isin(VINGROUP_TICKERS), "group"] = VINGROUP_GROUP

    # Safe numeric conversion (use .get to avoid KeyError)
    df["eps_ttm"] = pd.to_numeric(df.get("eps_ttm"), errors="coerce")
    df["bvps"]    = pd.to_numeric(df.get("bvps"), errors="coerce")
    if "shares" in df.columns:
        df["shares"] = pd.to_numeric(df["shares"], errors="coerce")

    # Normalize close price to full VND if KBS or VCI returned prices in thousands
    df["close"] = np.where((df["close"] > 0) & (df["close"] < 1000), df["close"] * 1000, df["close"])

    # PE = Price / EPS   (EPS must be positive — loss-making → NaN)
    df["pe"] = np.where(df["eps_ttm"] > 0, df["close"] / df["eps_ttm"], np.nan)
    # PB = Price / BVPS
    df["pb"] = np.where(df["bvps"] > 0, df["close"] / df["bvps"], np.nan)

    # Outlier filter (exempt Vingroup Ecosystem from PE_MAX/PB_MAX upper limits)
    is_vin = df["group"] == VINGROUP_GROUP
    df.loc[~is_vin & ((df["pe"] < PE_MIN) | (df["pe"] > PE_MAX)), "pe"] = np.nan
    df.loc[~is_vin & ((df["pb"] < PB_MIN) | (df["pb"] > PB_MAX)), "pb"] = np.nan
    df.loc[is_vin & (df["pe"] < PE_MIN), "pe"] = np.nan
    df.loc[is_vin & (df["pb"] < PB_MIN), "pb"] = np.nan

    df["date"] = date.today()
    df = df.drop_duplicates(subset=["ticker"], keep="first")

    n_pe = df["pe"].notna().sum()
    n_pb = df["pb"].notna().sum()
    log.info(f"PE/PB computed | valid PE: {n_pe}/{len(df)} | valid PB: {n_pb}/{len(df)}")

    ret_cols = ["date", "ticker", "close", "pe", "pb", "sector", "industry", "group"]
    for col in ["shares", "eps_ttm", "bvps"]:
        if col in df.columns:
            ret_cols.append(col)
    return df[ret_cols]


# ── Sector aggregation ────────────────────────────────────────────────────────
def aggregate_sectors(snapshot: pd.DataFrame) -> pd.DataFrame:
    """
    Compute sector-level descriptive statistics + Market-Cap Weighted PE/PB.
    Uses 'group' column (Vingroup → own bucket; others = sector name).
    Also emits a 'VN-Index' row for full market aggregate.
    """
    rows = []
    if "shares" not in snapshot.columns:
        snapshot["shares"] = np.nan
    snapshot["shares"] = pd.to_numeric(snapshot["shares"], errors="coerce").fillna(0)

    # 1. VN-Index (full market)
    pe = snapshot["pe"].dropna()
    pb = snapshot["pb"].dropna()
    pe_val = snapshot[snapshot["pe"].notna() & (snapshot["shares"] > 0)]
    w_pe = (pe_val["close"] * pe_val["shares"]).sum() / (pe_val["eps_ttm"] * pe_val["shares"]).sum() if len(pe_val) > 0 and (pe_val["eps_ttm"] * pe_val["shares"]).sum() > 0 else np.nan
    pb_val = snapshot[snapshot["pb"].notna() & (snapshot["shares"] > 0)]
    w_pb = (pb_val["close"] * pb_val["shares"]).sum() / (pb_val["bvps"] * pb_val["shares"]).sum() if len(pb_val) > 0 and (pb_val["bvps"] * pb_val["shares"]).sum() > 0 else np.nan

    rows.append({
        "date":        snapshot["date"].iloc[0] if len(snapshot) > 0 else date.today(),
        "group":       "VN-Index",
        "count":       len(snapshot),
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

    # 2. Each group
    for grp_name, grp in snapshot.groupby("group"):
        pe = grp["pe"].dropna()
        pb = grp["pb"].dropna()
        pe_val = grp[grp["pe"].notna() & (grp["shares"] > 0)]
        w_pe = (pe_val["close"] * pe_val["shares"]).sum() / (pe_val["eps_ttm"] * pe_val["shares"]).sum() if len(pe_val) > 0 and (pe_val["eps_ttm"] * pe_val["shares"]).sum() > 0 else np.nan
        pb_val = grp[grp["pb"].notna() & (grp["shares"] > 0)]
        w_pb = (pb_val["close"] * pb_val["shares"]).sum() / (pb_val["bvps"] * pb_val["shares"]).sum() if len(pb_val) > 0 and (pb_val["bvps"] * pb_val["shares"]).sum() > 0 else np.nan

        rows.append({
            "date":        grp["date"].iloc[0],
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
    return pd.DataFrame(rows).sort_values("median_pe").reset_index(drop=True)


# ── History update ────────────────────────────────────────────────────────────
def _append_parquet(new_df: pd.DataFrame, path: str, date_col: str = "date") -> None:
    """Idempotent append: remove today's rows if present, then concat."""
    p = Path(path)
    today = new_df[date_col].iloc[0]
    if p.exists():
        old = pd.read_parquet(p)
        old[date_col] = pd.to_datetime(old[date_col]).dt.date
        old = old[old[date_col] != today]
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        combined = new_df.copy()
    if date_col in combined.columns and "ticker" in combined.columns:
        combined = combined.drop_duplicates(subset=[date_col, "ticker"], keep="last")
    elif date_col in combined.columns and "group" in combined.columns:
        combined = combined.drop_duplicates(subset=[date_col, "group"], keep="last")
    else:
        combined = combined.drop_duplicates()
    combined[date_col] = pd.to_datetime(combined[date_col])
    combined.to_parquet(path, index=False)
    log.info(f"Updated {path}  ({len(combined)} rows, {combined[date_col].nunique()} days)")


def update_history(snapshot: pd.DataFrame, sector_agg: pd.DataFrame) -> None:
    today_str = str(date.today())
    _append_parquet(snapshot,    TICKER_HIST_FILE)
    _append_parquet(sector_agg,  SECTOR_HIST_FILE)

    # Human-readable daily CSV (semicolon for VN locale compatibility)
    csv_path = Path(DAILY_DIR) / f"pe_pb_{today_str}.csv"
    snapshot.to_csv(csv_path, index=False, sep=";")
    log.info(f"Daily CSV saved -> {csv_path}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    today = date.today()
    log.info(f"=== Daily PE/PB pipeline | {today} ===")

    register_vnstock()

    # 1. Load fundamentals cache
    fundamentals = load_fundamentals()
    tickers = fundamentals.index.tolist()
    log.info(f"Universe: {len(tickers)} tickers from fundamentals cache")

    # 2. Fetch close prices
    log.info("Fetching close prices via KBS price_board...")
    close_prices = fetch_close_prices(tickers)

    # 3. Compute PE / PB
    snapshot = compute_pe_pb(close_prices, fundamentals)

    # 4. Sector aggregation
    sector_agg = aggregate_sectors(snapshot)
    log.info("\nSector summary (top 10 by count):\n" +
             sector_agg.nlargest(10, "count")[
                 ["group", "count", "valid_pe", "median_pe", "median_pb"]
             ].to_string(index=False))

    # 5. Update history
    update_history(snapshot, sector_agg)

    log.info(f"=== Daily pipeline complete | {today} ===")


if __name__ == "__main__":
    main()

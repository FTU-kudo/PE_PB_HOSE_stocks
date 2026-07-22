"""
Daily P/E & P/B pipeline  (runs weekdays ~16:05 ICT = 09:05 UTC).

Flow
----
1.  Load fundamentals cache  (data/fundamentals.parquet)
    ↳ Warn if stale (>8 days), but still proceed.
2.  Fetch today's close prices for all HOSE tickers
    via KBS price_board (batch = 50 tickers / call).
3.  Compute:
        PE_daily = close_price  / eps_annual
        PB_daily = close_price  / bvps
    Both capped to [PE_MIN, PE_MAX] and [PB_MIN, PB_MAX].
4.  Sector aggregation (median, mean, IQR) per group.
5.  Append to ticker_history.parquet and sector_history.parquet.
6.  Save today's CSV snapshot for transparency.

Why annual EPS?
  VAS quarterly income statements are year-to-date cumulative (Q2 IS = H1 P&L).
  Deaccumulating to period-specific EPS before TTM summation is complex and
  error-prone in an automated pipeline. Using the last audited annual EPS is
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
    if "ticker" not in df.columns:
        df = df.reset_index()
    df["ticker"] = df["ticker"].str.upper()
    df = df[df["ticker"].astype(str).str.len() == 3]
    df = df.drop_duplicates(subset=["ticker"], keep="first")
    log.info(f"Fundamentals loaded: {len(df)} tickers  (cache age: {age} days)")
    return df.set_index("ticker")


# ── Fetch close prices ────────────────────────────────────────────────────────
def fetch_close_prices(tickers: list[str]) -> pd.Series:
    """
    Fetch today's close price for all tickers via KBS price_board.
    Returns pd.Series indexed by ticker.

    KBS price_board columns (29 total, verified from vnstock docs):
      symbol, exchange, reference_price, price_change, percent_change,
      open_price, high_price, low_price, close_price, average_price,
      total_trades, total_volume, total_value,
      bid_price_1/2/3, bid_vol_1/2/3, ask_price_1/2/3, ask_vol_1/2/3,
      foreign_buy_volume, foreign_sell_volume

    We target 'close_price'; fall back to 'match_price' or 'reference_price'.
    """
    from vnstock import Trading

    all_rows = []
    n_batches = (len(tickers) + PRICE_BOARD_BATCH - 1) // PRICE_BOARD_BATCH

    for i in range(0, len(tickers), PRICE_BOARD_BATCH):
        batch = tickers[i : i + PRICE_BOARD_BATCH]
        batch_no = i // PRICE_BOARD_BATCH + 1
        try:
            df = Trading(source="KBS").price_board(symbols_list=batch)
            all_rows.append(df)
            log.info(f"  price_board batch {batch_no}/{n_batches}: {len(df)} rows")
        except Exception as exc:
            log.warning(f"  price_board batch {batch_no}/{n_batches} failed: {exc}")
        if batch_no < n_batches:
            time.sleep(1.0)

    if not all_rows:
        log.error("All price_board batches failed. Aborting.")
        sys.exit(1)

    board = pd.concat(all_rows, ignore_index=True)

    # --- Detect ticker column ---
    ticker_col = next(
        (c for c in board.columns if c.lower() in ("symbol", "ticker", "code")),
        board.columns[0],
    )
    board = board.rename(columns={ticker_col: "ticker"})
    board["ticker"] = board["ticker"].str.upper()
    board = board[board["ticker"].astype(str).str.len() == 3]

    # --- Detect price column (priority order) ---
    cols_l = {c.lower(): c for c in board.columns}
    for candidate in ("close_price", "match_price", "close", "matchedprice",
                       "average_price", "reference_price"):
        if candidate in cols_l:
            price_col = cols_l[candidate]
            break
    else:
        # last resort: first numeric column that is not the ticker col
        price_col = next(
            c for c in board.columns if c != "ticker" and pd.api.types.is_numeric_dtype(board[c])
        )

    board["close"] = pd.to_numeric(board[price_col], errors="coerce")
    log.info(f"Close price column: '{price_col}'  | valid prices: {board['close'].notna().sum()}/{len(board)}")

    return board.set_index("ticker")["close"].dropna()


# ── Compute PE / PB ───────────────────────────────────────────────────────────
def compute_pe_pb(close: pd.Series, fundamentals: pd.DataFrame) -> pd.DataFrame:
    """
    Merge daily close with cached EPS / BVPS and compute PE / PB.

    PE = close / eps_annual
    PB = close / bvps

    Both are windsorised to [PE_MIN, PE_MAX] and [PB_MIN, PB_MAX]:
    values outside the range become NaN (not capped) so they do not
    distort sector medians.
    """
    df = close.rename("close").reset_index()
    df.columns = ["ticker", "close"]

    fund = fundamentals[["eps_annual", "bvps",
                          "sector", "industry", "group"]].copy()
    df = df.merge(fund, on="ticker", how="left")

    # Fill missing group for non-fundamentals tickers
    df["sector"]   = df["sector"].fillna("Unknown")
    df["industry"] = df["industry"].fillna("Unknown")
    df["group"]    = df["group"].fillna("Unknown")

    # Vingroup override (in case sector map was stale)
    mask = df["ticker"].isin(VINGROUP_TICKERS)
    df.loc[mask, "group"] = VINGROUP_GROUP

    df["eps_annual"] = pd.to_numeric(df["eps_annual"], errors="coerce")
    df["bvps"]       = pd.to_numeric(df["bvps"],       errors="coerce")

    # Normalize close price to full VND if KBS or VCI returned prices in thousands
    df["close"] = np.where((df["close"] > 0) & (df["close"] < 1000), df["close"] * 1000, df["close"])

    # PE = Price / EPS   (EPS must be positive — loss-making → NaN)
    df["pe"] = np.where(df["eps_annual"] > 0, df["close"] / df["eps_annual"], np.nan)
    # PB = Price / BVPS
    df["pb"] = np.where(df["bvps"] > 0, df["close"] / df["bvps"], np.nan)

    # Outlier filter → NaN (not capped)
    df.loc[(df["pe"] < PE_MIN) | (df["pe"] > PE_MAX), "pe"] = np.nan
    df.loc[(df["pb"] < PB_MIN) | (df["pb"] > PB_MAX), "pb"] = np.nan

    df["date"] = date.today()
    df = df.drop_duplicates(subset=["ticker"], keep="first")

    n_pe = df["pe"].notna().sum()
    n_pb = df["pb"].notna().sum()
    log.info(f"PE/PB computed | valid PE: {n_pe}/{len(df)} | valid PB: {n_pb}/{len(df)}")
    return df[["date", "ticker", "close", "pe", "pb", "sector", "industry", "group"]]


# ── Sector aggregation ────────────────────────────────────────────────────────
def aggregate_sectors(snapshot: pd.DataFrame) -> pd.DataFrame:
    """
    Compute sector-level descriptive statistics.
    Uses 'group' column (Vingroup → own bucket; others = sector name).
    Metrics: count, valid_pe, valid_pb, median_pe, median_pb,
             mean_pe, mean_pb, p25_pe, p75_pe, p25_pb, p75_pb.
    """
    rows = []
    for grp_name, grp in snapshot.groupby("group"):
        pe = grp["pe"].dropna()
        pb = grp["pb"].dropna()
        rows.append({
            "date":      grp["date"].iloc[0],
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

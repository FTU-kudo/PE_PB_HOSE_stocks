"""
Daily P/E & P/B pipeline  (runs weekdays ~16:05 ICT = 09:05 UTC).
"""

import os, sys, time, logging, warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

for d in [DATA_DIR, DAILY_DIR, "docs"]:
    Path(d).mkdir(parents=True, exist_ok=True)


def register_vnstock() -> None:
    api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
    if not api_key:
        log.warning("VNSTOCK_API_KEY not set — running as Guest (20 req/min).")
        return
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
            return
        except (ImportError, AttributeError):
            continue
        except Exception as exc:
            log.warning(f"{module}.{func} raised: {exc}")
            break
    log.warning("Could not register API key. Guest mode.")


def load_fundamentals() -> pd.DataFrame:
    fund_path = Path(FUND_FILE)
    if not fund_path.exists():
        log.error(f"{FUND_FILE} not found. Run fetch_fundamentals.py first.")
        sys.exit(1)
    mtime = date.fromtimestamp(fund_path.stat().st_mtime)
    age   = (date.today() - mtime).days
    if age > FUND_STALE_DAYS:
        log.warning(f"Fundamentals cache is {age} days old (threshold={FUND_STALE_DAYS}).")
    df = pd.read_parquet(fund_path)
    if "ticker" not in df.columns:
        df = df.reset_index()
    df["ticker"] = df["ticker"].str.upper()
    df = df[df["ticker"].astype(str).str.len() == 3]
    df = df.drop_duplicates(subset=["ticker"], keep="first")
    log.info(f"Fundamentals loaded: {len(df)} tickers  (cache age: {age} days)")
    return df.set_index("ticker")


def fetch_close_prices(tickers: list[str]) -> pd.Series:
    from vnstock import Trading
    all_rows  = []
    n_batches = (len(tickers) + PRICE_BOARD_BATCH - 1) // PRICE_BOARD_BATCH
    for i in range(0, len(tickers), PRICE_BOARD_BATCH):
        batch    = tickers[i : i + PRICE_BOARD_BATCH]
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
    ticker_col = next(
        (c for c in board.columns if c.lower() in ("symbol", "ticker", "code")),
        board.columns[0])
    board = board.rename(columns={ticker_col: "ticker"})
    board["ticker"] = board["ticker"].str.upper()
    board = board[board["ticker"].astype(str).str.len() == 3]
    cols_l = {c.lower(): c for c in board.columns}
    for candidate in ("close_price", "match_price", "close", "matchedprice",
                      "average_price", "reference_price"):
        if candidate in cols_l:
            price_col = cols_l[candidate]
            break
    else:
        price_col = next(
            c for c in board.columns
            if c != "ticker" and pd.api.types.is_numeric_dtype(board[c]))
    board["close"] = pd.to_numeric(board[price_col], errors="coerce")
    log.info(f"Close price col: '{price_col}' | valid: {board['close'].notna().sum()}/{len(board)}")
    return board.set_index("ticker")["close"].dropna()


def compute_pe_pb(close: pd.Series, fundamentals: pd.DataFrame) -> pd.DataFrame:
    df = close.rename("close").reset_index()
    df.columns = ["ticker", "close"]

    # ── eps_ttm is the column name saved by fetch_fundamentals.py ──────────
    fund_cols = ["eps_ttm", "bvps", "sector", "industry", "group"]
    if "shares" in fundamentals.columns:
        fund_cols.append("shares")
    fund = fundamentals[[c for c in fund_cols if c in fundamentals.columns]].copy()
    df   = df.merge(fund, on="ticker", how="left")

    for col in ("sector", "industry", "group"):
        df[col] = df[col].fillna("Unknown") if col in df.columns else "Unknown"

    mask_vin = df["ticker"].isin(VINGROUP_TICKERS)
    df.loc[mask_vin, "group"] = VINGROUP_GROUP

    df["eps_ttm"] = pd.to_numeric(df["eps_ttm"], errors="coerce")
    df["bvps"]       = pd.to_numeric(df["bvps"],       errors="coerce")

    # Normalise price if returned in thousands
    df["close"] = np.where(
        (df["close"] > 0) & (df["close"] < 1000),
        df["close"] * 1000, df["close"])

    df["pe"] = np.where(df["eps_ttm"] > 0, df["close"] / df["eps_ttm"], np.nan)
    df["pb"] = np.where(df["bvps"]       > 0, df["close"] / df["bvps"],       np.nan)

    is_vin = df["group"] == VINGROUP_GROUP
    df.loc[~is_vin & ((df["pe"] < PE_MIN) | (df["pe"] > PE_MAX)), "pe"] = np.nan
    df.loc[~is_vin & ((df["pb"] < PB_MIN) | (df["pb"] > PB_MAX)), "pb"] = np.nan
    df.loc[ is_vin &  (df["pe"] < PE_MIN), "pe"] = np.nan
    df.loc[ is_vin &  (df["pb"] < PB_MIN), "pb"] = np.nan

    df["date"] = date.today()
    df = df.drop_duplicates(subset=["ticker"], keep="first")

    n_pe = df["pe"].notna().sum()
    n_pb = df["pb"].notna().sum()
    log.info(f"PE/PB computed | valid PE: {n_pe}/{len(df)} | valid PB: {n_pb}/{len(df)}")

    ret_cols = ["date", "ticker", "close", "pe", "pb", "sector", "industry", "group"]
    for col in ("shares", "eps_ttm", "bvps"):
        if col in df.columns:
            ret_cols.append(col)
    return df[ret_cols]


def aggregate_sectors(snapshot: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if "shares" not in snapshot.columns:
        snapshot["shares"] = np.nan
    snapshot["shares"] = pd.to_numeric(snapshot["shares"], errors="coerce").fillna(0)

    def _w_pe(grp):
        v = grp[grp["pe"].notna() & (grp["shares"] > 0) & (grp["eps_ttm"] > 0)]
        denom = (v["eps_ttm"] * v["shares"]).sum()
        return (v["close"] * v["shares"]).sum() / denom if denom > 0 else np.nan

    def _w_pb(grp):
        v = grp[grp["pb"].notna() & (grp["shares"] > 0) & (grp["bvps"] > 0)]
        denom = (v["bvps"] * v["shares"]).sum()
        return (v["close"] * v["shares"]).sum() / denom if denom > 0 else np.nan

    def _row(label, grp):
        pe = grp["pe"].dropna()
        pb = grp["pb"].dropna()
        return {
            "date":        grp["date"].iloc[0],
            "group":       label,
            "count":       len(grp),
            "valid_pe":    len(pe),
            "valid_pb":    len(pb),
            "median_pe":   pe.median()      if len(pe) else np.nan,
            "median_pb":   pb.median()      if len(pb) else np.nan,
            "mean_pe":     pe.mean()        if len(pe) else np.nan,
            "mean_pb":     pb.mean()        if len(pb) else np.nan,
            "weighted_pe": _w_pe(grp)       if "eps_ttm" in grp.columns else np.nan,
            "weighted_pb": _w_pb(grp)       if "bvps" in grp.columns else np.nan,
            "p25_pe":      pe.quantile(.25) if len(pe) else np.nan,
            "p75_pe":      pe.quantile(.75) if len(pe) else np.nan,
            "p25_pb":      pb.quantile(.25) if len(pb) else np.nan,
            "p75_pb":      pb.quantile(.75) if len(pb) else np.nan,
        }

    rows.append(_row("VN-Index", snapshot))
    for grp_name, grp in snapshot.groupby("group"):
        rows.append(_row(grp_name, grp))

    return pd.DataFrame(rows).sort_values("median_pe").reset_index(drop=True)


def _append_parquet(new_df: pd.DataFrame, path: str, date_col: str = "date") -> None:
    p = Path(path)
    today = new_df[date_col].iloc[0]
    if p.exists():
        old = pd.read_parquet(p)
        old[date_col] = pd.to_datetime(old[date_col]).dt.date
        old = old[old[date_col] != today]
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        combined = new_df.copy()
    key = [date_col, "ticker"] if "ticker" in combined.columns else \
          [date_col, "group"]  if "group"  in combined.columns else [date_col]
    combined = combined.drop_duplicates(subset=key, keep="last")
    combined[date_col] = pd.to_datetime(combined[date_col])
    combined.to_parquet(path, index=False)
    log.info(f"Updated {path}  ({len(combined)} rows, {combined[date_col].nunique()} days)")


def update_history(snapshot: pd.DataFrame, sector_agg: pd.DataFrame) -> None:
    today_str = str(date.today())
    _append_parquet(snapshot,   TICKER_HIST_FILE)
    _append_parquet(sector_agg, SECTOR_HIST_FILE)
    csv_path = Path(DAILY_DIR) / f"pe_pb_{today_str}.csv"
    snapshot.to_csv(csv_path, index=False, sep=";")
    log.info(f"Daily CSV saved -> {csv_path}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    today = date.today()
    log.info(f"=== Daily PE/PB pipeline | {today} ===")
    register_vnstock()
    fundamentals = load_fundamentals()
    tickers      = fundamentals.index.tolist()
    log.info(f"Universe: {len(tickers)} tickers from fundamentals cache")
    close_prices = fetch_close_prices(tickers)
    snapshot     = compute_pe_pb(close_prices, fundamentals)
    sector_agg   = aggregate_sectors(snapshot)
    log.info("\nSector summary (top 10 by count):\n" +
             sector_agg.nlargest(10, "count")[
                 ["group", "count", "valid_pe", "median_pe", "median_pb"]
             ].to_string(index=False))
    update_history(snapshot, sector_agg)
    log.info(f"=== Daily pipeline complete | {today} ===")


if __name__ == "__main__":
    main()

"""
Daily P/E & P/B pipeline (runs weekdays ~16:05 ICT = 09:05 UTC).
PE = close / eps_ttm    PB = close / bvps
"""
import os, sys, time, logging, warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv; load_dotenv()
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
        log.warning("VNSTOCK_API_KEY not set — Guest mode.")
        return
    for module, func in [
        ("vnstock", "register_user"),
        ("vnstock.common.user", "register_user"),
        ("vnstock", "init_user"),
    ]:
        try:
            mod = __import__(module, fromlist=[func])
            getattr(mod, func)(api_key=api_key)
            log.info(f"vnstock registered via {module}.{func}")
            return
        except (ImportError, AttributeError):
            continue
        except Exception as exc:
            log.warning(f"{module}.{func}: {exc}"); break
    log.warning("Could not register. Guest mode.")


def load_fundamentals() -> pd.DataFrame:
    fund_path = Path(FUND_FILE)
    if not fund_path.exists():
        log.error(f"{FUND_FILE} not found. Run fetch_fundamentals.py first.")
        sys.exit(1)
    age = (date.today() - date.fromtimestamp(fund_path.stat().st_mtime)).days
    if age > FUND_STALE_DAYS:
        log.warning(f"Fundamentals cache is {age} days old.")
    df = pd.read_parquet(fund_path)
    if "ticker" not in df.columns:
        df = df.reset_index()
    df["ticker"] = df["ticker"].str.upper()
    df = df[df["ticker"].astype(str).str.len() == 3]
    df = df.drop_duplicates(subset=["ticker"], keep="first")

    # Validate required columns exist
    for col in ("eps_ttm", "bvps"):
        if col not in df.columns:
            log.error(f"Column '{col}' missing from fundamentals.parquet. "
                      f"Available: {list(df.columns)}")
            sys.exit(1)

    log.info(f"Fundamentals loaded: {len(df)} tickers (cache age: {age} days) | "
             f"TTM EPS valid: {df['eps_ttm'].notna().sum()} | "
             f"BVPS valid: {df['bvps'].notna().sum()}")
    return df.set_index("ticker")


def fetch_daily_market_data(tickers: list[str]) -> pd.DataFrame:
    from vnstock import Trading
    all_rows  = []
    n_batches = (len(tickers) + PRICE_BOARD_BATCH - 1) // PRICE_BOARD_BATCH
    for i in range(0, len(tickers), PRICE_BOARD_BATCH):
        batch    = tickers[i: i + PRICE_BOARD_BATCH]
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
        log.error("All price_board batches failed.")
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
    board = board.dropna(subset=["close"]).drop_duplicates(subset=["ticker"])

    try:
        vci = Trading(source="VCI").price_board(symbols_list=tickers)
        if ('listing', 'listed_share') in vci.columns:
            shares = vci[[('listing', 'symbol'), ('listing', 'listed_share')]].copy()
            shares.columns = ["ticker", "shares"]
            shares["ticker"] = shares["ticker"].str.upper()
            shares["shares"] = pd.to_numeric(shares["shares"], errors="coerce")
            board = board.merge(shares, on="ticker", how="left")
            log.info(f"Fetched shares from VCI | valid: {board['shares'].notna().sum()}/{len(board)}")
        else:
            board["shares"] = np.nan
    except Exception as exc:
        log.warning(f"Failed to fetch shares from VCI: {exc}")
        board["shares"] = np.nan

    log.info(f"Close price col: '{price_col}' | valid: {board['close'].notna().sum()}/{len(board)}")
    return board[["ticker", "close", "shares"]]


def compute_pe_pb(market_data: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.DataFrame:
    df = market_data.copy()

    fund_cols = ["eps_ttm", "bvps", "sector", "industry", "group"]
    
    if "shares" in fundamentals.columns:
        df = df.merge(fundamentals[["shares"]].rename(columns={"shares": "fund_shares"}), on="ticker", how="left")
        df["shares"] = df["shares"].fillna(df["fund_shares"])
        df = df.drop(columns=["fund_shares"])

    fund = fundamentals[[c for c in fund_cols if c in fundamentals.columns]].copy()
    df   = df.merge(fund, on="ticker", how="left")

    for col in ("sector", "industry", "group"):
        if col not in df.columns: df[col] = "Unknown"
        df[col] = df[col].fillna("Unknown")

    mask_vin = df["ticker"].isin(VINGROUP_TICKERS)
    df.loc[mask_vin, "group"] = VINGROUP_GROUP

    df["eps_ttm"] = pd.to_numeric(df["eps_ttm"], errors="coerce")
    df["bvps"]    = pd.to_numeric(df["bvps"],    errors="coerce")

    # Normalise price if returned in thousands
    df["close"] = np.where(
        (df["close"] > 0) & (df["close"] < 1000),
        df["close"] * 1000, df["close"])

    df["pe"] = np.where(df["eps_ttm"] > 0, df["close"] / df["eps_ttm"], np.nan)
    df["pb"] = np.where(df["bvps"]    > 0, df["close"] / df["bvps"],    np.nan)

    is_vin = df["group"] == VINGROUP_GROUP
    df.loc[~is_vin & ((df["pe"] < PE_MIN) | (df["pe"] > PE_MAX)), "pe"] = np.nan
    df.loc[~is_vin & ((df["pb"] < PB_MIN) | (df["pb"] > PB_MAX)), "pb"] = np.nan
    df.loc[ is_vin &  (df["pe"] < PE_MIN), "pe"] = np.nan
    df.loc[ is_vin &  (df["pb"] < PB_MIN), "pb"] = np.nan

    df["date"] = date.today()
    df = df.drop_duplicates(subset=["ticker"], keep="first")
    log.info(f"PE/PB computed | PE valid: {df['pe'].notna().sum()}/{len(df)} | "
             f"PB valid: {df['pb'].notna().sum()}/{len(df)}")

    ret_cols = ["date", "ticker", "close", "pe", "pb", "sector", "industry", "group"]
    for col in ("shares", "eps_ttm", "bvps"):
        if col in df.columns:
            ret_cols.append(col)
    return df[ret_cols]


def aggregate_sectors(snapshot: pd.DataFrame) -> pd.DataFrame:
    if "shares" not in snapshot.columns:
        snapshot = snapshot.copy()
        snapshot["shares"] = np.nan
    snapshot["shares"] = pd.to_numeric(snapshot["shares"], errors="coerce").fillna(0)

    def _row(label, grp):
        pe = grp["pe"].dropna()
        pb = grp["pb"].dropna()
        
        sum_pe_mc, sum_pe_ern, w_pe = np.nan, np.nan, np.nan
        if "eps_ttm" in grp.columns:
            v_pe = grp[grp["pe"].notna() & (grp["shares"] > 0) & (grp["eps_ttm"] > 0)]
            sum_pe_mc = (v_pe["close"] * v_pe["shares"]).sum()
            sum_pe_ern = (v_pe["eps_ttm"] * v_pe["shares"]).sum()
            if len(v_pe) > 0 and sum_pe_ern > 0:
                w_pe = sum_pe_mc / sum_pe_ern

        sum_pb_mc, sum_pb_bv, w_pb = np.nan, np.nan, np.nan
        if "bvps" in grp.columns:
            v_pb = grp[grp["pb"].notna() & (grp["shares"] > 0) & (grp["bvps"] > 0)]
            sum_pb_mc = (v_pb["close"] * v_pb["shares"]).sum()
            sum_pb_bv = (v_pb["bvps"] * v_pb["shares"]).sum()
            if len(v_pb) > 0 and sum_pb_bv > 0:
                w_pb = sum_pb_mc / sum_pb_bv

        return {
            "date": grp["date"].iloc[0], "group": label,
            "count": len(grp), "valid_pe": len(pe), "valid_pb": len(pb),
            "median_pe": pe.median() if len(pe) else np.nan,
            "median_pb": pb.median() if len(pb) else np.nan,
            "mean_pe":   pe.mean()   if len(pe) else np.nan,
            "mean_pb":   pb.mean()   if len(pb) else np.nan,
            "weighted_pe": w_pe,
            "weighted_pb": w_pb,
            "sum_pe_mc": sum_pe_mc, "sum_pe_ern": sum_pe_ern,
            "sum_pb_mc": sum_pb_mc, "sum_pb_bv": sum_pb_bv,
            "p25_pe": pe.quantile(.25) if len(pe) else np.nan,
            "p75_pe": pe.quantile(.75) if len(pe) else np.nan,
            "p25_pb": pb.quantile(.25) if len(pb) else np.nan,
            "p75_pb": pb.quantile(.75) if len(pb) else np.nan,
        }

    rows = [_row("VN-Index", snapshot)]
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
    key = ([date_col, "ticker"] if "ticker" in combined.columns else
           [date_col, "group"]  if "group"  in combined.columns else [date_col])
    combined = combined.drop_duplicates(subset=key, keep="last")
    combined[date_col] = pd.to_datetime(combined[date_col])
    combined.to_parquet(path, index=False)
    log.info(f"Updated {path} ({len(combined)} rows, {combined[date_col].nunique()} days)")


def update_history(snapshot: pd.DataFrame, sector_agg: pd.DataFrame) -> None:
    _append_parquet(snapshot,   TICKER_HIST_FILE)
    _append_parquet(sector_agg, SECTOR_HIST_FILE)
    csv_path = Path(DAILY_DIR) / f"pe_pb_{date.today()}.csv"
    snapshot.to_csv(csv_path, index=False, sep=";")
    log.info(f"Daily CSV saved → {csv_path}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    today = date.today()
    log.info(f"=== Daily PE/PB pipeline | {today} ===")
    register_vnstock()
    fundamentals = load_fundamentals()
    tickers      = fundamentals.index.tolist()
    log.info(f"Universe: {len(tickers)} tickers")
    market_data  = fetch_daily_market_data(tickers)
    snapshot     = compute_pe_pb(market_data, fundamentals)
    sector_agg   = aggregate_sectors(snapshot)
    log.info("\nSector summary (top 10):\n" +
             sector_agg.nlargest(10, "count")[
                 ["group", "count", "valid_pe", "median_pe", "median_pb"]
             ].to_string(index=False))
    update_history(snapshot, sector_agg)
    log.info(f"=== Daily pipeline complete | {today} ===")


if __name__ == "__main__":
    main()

"""
Weekly fundamentals refresh  (run every Sunday ~01:00 UTC via GitHub Actions).
For each HOSE ticker, fetches Fundamental.equity(ticker).ratio(period='year')
and extracts:
  - eps_annual : EPS of the most recent complete fiscal year
  - bvps       : Book Value Per Share from the most recent annual report

Design notes
------------
- We use ANNUAL EPS (not TTM) to avoid cumulative-quarter deaccumulation.
  VAS quarterly IS statements are year-to-date cumulative, so Q2 IS contains
  H1 revenue. Using the last full fiscal year is safe, audited, and avoids
  that deaccumulation trap.
- BVPS comes from the most recent annual balance sheet ratio row.
- Banks / financial firms follow SBV Circular 49, not Circular 200, so their
  equity structure differs — but Fundamental.ratio() handles this at the API level.
- Results are written to data/fundamentals.parquet (ticker as index).
- Compatible with Vnstock 4.0 Unified UI (Fundamental class).
"""

import os
import sys
import time
import logging
import warnings
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

if '__file__' in globals():
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
else:
    PROJECT_ROOT = Path.cwd()

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import (
    EXCHANGE, FUND_FILE,
    FUND_BATCH_SLEEP, FUND_BATCH_EVERY, FUND_BATCH_PAUSE,
    FUND_MAX_RETRIES, FUND_RETRY_WAIT,
    DATA_DIR, DAILY_DIR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

for d in [DATA_DIR, DAILY_DIR, "docs"]:
    Path(d).mkdir(parents=True, exist_ok=True)


def register_vnstock() -> None:
    api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
    if not api_key:
        log.warning("VNSTOCK_API_KEY not set — running as Guest (20 req/min).")
        return
    try:
        from vnstock import set_api_key
        set_api_key(api_key)
        log.info("vnstock API key registered via set_api_key (60 req/min).")
        return
    except (ImportError, AttributeError):
        pass
    if os.getenv("VNSTOCK_API_KEY") != api_key:
        os.environ["VNSTOCK_API_KEY"] = api_key
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
        log.warning("Could not register API key via any method. Guest mode may apply.")


def get_hose_tickers() -> list[str]:
    log.info("Fetching HOSE ticker universe...")
    from vnstock import Reference
    ref = Reference()
    df = None
    strategies = [
        ("positional HOSE",     lambda: ref.equity.list_by_exchange("HOSE")),
        ("positional HSX",      lambda: ref.equity.list_by_exchange("HSX")),
        ("no-arg all",          lambda: ref.equity.list_by_exchange()),
        ("listing.all_symbols", lambda: ref.listing.all_symbols()),
    ]
    for label, fn in strategies:
        try:
            result = fn()
            if result is not None and not result.empty:
                df = result
                log.info(f"  Strategy '{label}' succeeded ({len(df)} rows).")
                break
        except Exception as exc:
            log.debug(f"  Strategy '{label}' failed: {exc}")

    if df is None or df.empty:
        raise RuntimeError("All strategies to fetch HOSE tickers failed.")

    exchange_col = next(
        (c for c in df.columns
         if c.lower() in ("exchange", "san", "listing_on", "comgroupcode", "market")),
        None,
    )
    if exchange_col:
        before = len(df)
        df = df[df[exchange_col].str.upper().isin(["HOSE", "HSX"])]
        log.info(f"  Exchange filter: {before} → {len(df)} via '{exchange_col}'.")

    ticker_col = next(
        (c for c in df.columns
         if c.lower() in ("ticker", "symbol", "code", "stock_code")),
        df.columns[0],
    )
    raw_tickers = df[ticker_col].dropna().str.upper().str.strip()
    type_col = next(
        (c for c in df.columns
         if c.lower() in ("type", "instrument_type", "security_type",
                          "comtypecode", "sectype", "assettype")),
        None,
    )
    if type_col:
        stock_types = {"stock", "equity", "s", "cp", "commonstock",
                       "common_stock", "cổ phiếu"}
        mask = df[type_col].str.lower().isin(stock_types)
        before = len(raw_tickers)
        raw_tickers = raw_tickers[mask]
        log.info(f"  Type filter (col='{type_col}'): {before} → {len(raw_tickers)} stocks.")
    else:
        before = len(raw_tickers)
        raw_tickers = raw_tickers[raw_tickers.str.match(r'^[A-Z]{3}$')]
        log.info(f"  Regex filter (^[A-Z]{{3}}$): {before} → {len(raw_tickers)} stocks.")

    tickers = raw_tickers.tolist()
    log.info(f"  → {len(tickers)} HOSE stocks ready.")
    return tickers


def get_sector_map(tickers: list[str]) -> pd.DataFrame:
    from scripts.config import VINGROUP_TICKERS, VINGROUP_GROUP
    from vnstock import Reference
    ref  = Reference()
    base = pd.DataFrame({"ticker": tickers})
    base["sector"]   = "Unknown"
    base["industry"] = "Unknown"
    raw = None
    for label, fn in [
        ("VCI list_by_industry", lambda: ref.equity.list_by_industry()),
        ("KBS sectors",          lambda: ref.industry.sectors()),
    ]:
        try:
            result = fn()
            if result is not None and not result.empty:
                raw = result
                log.info(f"Sector data from {label}: {len(raw)} rows | cols: {list(raw.columns)}")
                break
        except Exception as exc:
            log.warning(f"Sector source '{label}' failed: {exc}")

    if raw is None:
        log.warning("No sector data — all tickers will be 'Unknown'.")
        base["group"] = "Unknown"
        mask = base["ticker"].isin(VINGROUP_TICKERS)
        base.loc[mask, "group"] = VINGROUP_GROUP
        return base

    cols = list(raw.columns)
    cl   = [c.lower() for c in cols]
    ticker_col = next(
        (cols[i] for i, c in enumerate(cl)
         if c in ("ticker", "symbol", "code", "stockcode", "stock_code")), None)
    sector_col = next(
        (cols[i] for i, c in enumerate(cl)
         if any(p in c for p in ("sector", "linh_vuc", "icbname", "icb_name",
                                  "groupname", "group_name"))), None)
    industry_col = next(
        (cols[i] for i, c in enumerate(cl)
         if any(p in c for p in ("industry", "nganh", "industryname"))), None)
    log.info(f"  Detected → ticker='{ticker_col}' | sector='{sector_col}' | industry='{industry_col}'")

    if ticker_col is None:
        log.warning("Could not detect ticker column in sector data.")
        base["group"] = "Unknown"
        mask = base["ticker"].isin(VINGROUP_TICKERS)
        base.loc[mask, "group"] = VINGROUP_GROUP
        return base

    lookup = pd.DataFrame({"ticker": raw[ticker_col].astype(str).str.upper().str.strip()})
    if sector_col:
        lookup["sector"]   = raw[sector_col].values
    if industry_col:
        lookup["industry"] = raw[industry_col].values
    lookup = lookup.drop_duplicates(subset="ticker", keep="first")

    base = base.drop(columns=["sector", "industry"], errors="ignore")
    base = base.merge(lookup, on="ticker", how="left")
    for col in ("sector", "industry"):
        if col not in base.columns:
            base[col] = "Unknown"
        base[col] = base[col].fillna("Unknown")
    base["group"] = base["sector"].copy()
    mask = base["ticker"].isin(VINGROUP_TICKERS)
    base.loc[mask, "group"] = VINGROUP_GROUP
    log.info(f"Sector map complete. Top groups: {base['group'].value_counts().head(5).to_dict()}")
    return base


def _extract_eps_bvps(ratio_df: pd.DataFrame, ticker: str) -> dict:
    null = {"ticker": ticker, "eps_annual": np.nan, "bvps": np.nan,
            "fetched_date": str(date.today())}
    if ratio_df is None or ratio_df.empty:
        return null
    if 'item_en' in ratio_df.columns:
        name_col = 'item_en'
    elif 'item' in ratio_df.columns:
        name_col = 'item'
    else:
        return null
    year_cols = [c for c in ratio_df.columns if str(c).isdigit() and len(str(c)) == 4]
    if not year_cols:
        return null
    latest_year = sorted(year_cols)[-1]
    eps_mask = ratio_df[name_col].str.contains('EPS|Earnings Per Share', case=False, na=False)
    eps_row  = ratio_df[eps_mask]
    eps = np.nan
    if not eps_row.empty:
        eps = pd.to_numeric(eps_row.iloc[0][latest_year], errors='coerce')
        if not np.isnan(eps) and eps < 0:
            eps = np.nan
    bvps_mask = ratio_df[name_col].str.contains('BVPS|Book Value Per Share', case=False, na=False)
    bvps_row  = ratio_df[bvps_mask]
    bvps = np.nan
    if not bvps_row.empty:
        bvps = pd.to_numeric(bvps_row.iloc[0][latest_year], errors='coerce')
    return {"ticker": ticker, "eps_annual": eps, "bvps": bvps,
            "fetched_date": str(date.today())}


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "rate limit", "rate_limit",
                                  "throttl", "too many request", "exceeded"))


def fetch_all_fundamentals(tickers: list[str]) -> pd.DataFrame:
    from vnstock import Fundamental
    fun     = Fundamental()
    records = []
    n       = len(tickers)
    null    = lambda t: {"ticker": t, "eps_annual": np.nan, "bvps": np.nan,
                         "fetched_date": str(date.today())}

    for i, ticker in enumerate(tickers, 1):
        success = False
        for attempt in range(1, FUND_MAX_RETRIES + 1):
            try:
                ratio_df = fun.equity(ticker).ratio(period="year", orient="report", lang="en")
                records.append(_extract_eps_bvps(ratio_df, ticker))
                success = True
                break
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    wait = FUND_RETRY_WAIT * attempt
                    log.warning(f"  [{i}/{n}] {ticker} — rate limit (attempt {attempt}). Sleeping {wait}s...")
                    time.sleep(wait)
                else:
                    log.debug(f"  [{i}/{n}] {ticker} — attempt {attempt} failed: {exc}")
                    if attempt < FUND_MAX_RETRIES:
                        time.sleep(5)
        if not success:
            log.warning(f"  [{i}/{n}] {ticker} — gave up after {FUND_MAX_RETRIES} attempts.")
            records.append(null(ticker))
        if i == 1 or i % 25 == 0:
            done = sum(1 for r in records if not np.isnan(r.get("eps_annual", np.nan)))
            log.info(f"  Progress {i}/{n} | EPS fetched so far: {done}")
        time.sleep(FUND_BATCH_SLEEP)
        if i % FUND_BATCH_EVERY == 0 and i < n:
            log.info(f"  Batch pause after {i} tickers — sleeping {FUND_BATCH_PAUSE}s...")
            time.sleep(FUND_BATCH_PAUSE)

    df         = pd.DataFrame(records).set_index("ticker")
    valid_eps  = df["eps_annual"].notna().sum()
    valid_bvps = df["bvps"].notna().sum()
    log.info(f"Fundamentals complete: {len(df)} tickers | "
             f"EPS valid: {valid_eps} ({valid_eps/len(df)*100:.0f}%) | "
             f"BVPS valid: {valid_bvps} ({valid_bvps/len(df)*100:.0f}%)")
    return df


def main():
    log.info("=== Weekly fundamentals refresh started ===")
    register_vnstock()

    tickers    = get_hose_tickers()
    sector_map = get_sector_map(tickers)

    fundamentals = fetch_all_fundamentals(tickers)

    merged = fundamentals.reset_index().merge(
        sector_map[["ticker", "sector", "industry", "group"]],
        on="ticker", how="left"
    )

    # ── Fix: after left-join some rows may have NaN in string columns.
    # PyArrow refuses to write a column that mixes float NaN with str values.
    # Cast every string column to str explicitly (NaN → "Unknown").
    for col in ["sector", "industry", "group", "fetched_date"]:
        if col in merged.columns:
            merged[col] = merged[col].fillna("Unknown").astype(str)

    merged.to_parquet(FUND_FILE, index=False)
    log.info(f"Fundamentals saved → {FUND_FILE}  ({len(merged)} rows)")
    log.info("=== Weekly fundamentals refresh complete ===")


if __name__ == "__main__":
    main()

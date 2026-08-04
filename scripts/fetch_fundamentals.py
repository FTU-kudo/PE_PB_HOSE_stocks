"""
Weekly fundamentals refresh — fetches TTM EPS and latest quarterly BVPS
for all HOSE stocks via vnstock API (Vnstock 4.0 compatible).
"""

import os, sys, time, logging, warnings
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

if "__file__" in globals():
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

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

for d in [DATA_DIR, DAILY_DIR, "docs"]:
    Path(d).mkdir(parents=True, exist_ok=True)

# ── Auth (Vnstock 4.0) ────────────────────────────────────────────────────────
def register_vnstock() -> None:
    api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
    if not api_key:
        log.warning("VNSTOCK_API_KEY not set — Guest mode (rate limits apply).")
        return
    try:
        from vnstock import Vnstock
        # Vnstock 4.0 uses unified user registration
        Vnstock().user.register(api_key=api_key)
        log.info("vnstock API key registered successfully.")
        return
    except Exception as exc:
        log.warning(f"Could not register API key: {exc}. Continuing as Guest.")

# ── Ticker discovery ──────────────────────────────────────────────────────────
def get_hose_tickers() -> list[str]:
    log.info("Fetching HOSE ticker universe...")
    from vnstock import Vnstock
    listing = Vnstock().stock(source='VCI').listing
    df = None
    
    for label, fn in [
        ("symbols_by_exchange HOSE", lambda: listing.symbols_by_exchange(exchange="HOSE")),
        ("symbols_by_exchange HSX",  lambda: listing.symbols_by_exchange(exchange="HSX")),
        ("all_symbols",              lambda: listing.all_symbols()),
    ]:
        try:
            r = fn()
            if r is not None and not r.empty:
                df = r
                log.info(f"  Strategy '{label}' OK ({len(df)} rows).")
                break
        except Exception as exc:
            log.debug(f"  Strategy '{label}' failed: {exc}")

    if df is None or df.empty:
        raise RuntimeError("All strategies to fetch HOSE tickers failed.")

    ticker_col = next(
        (c for c in df.columns if c.lower() in
         ("ticker", "symbol", "code", "stock_code")), df.columns[0])
    raw = df[ticker_col].dropna().astype(str).str.upper().str.strip()

    type_col = next(
        (c for c in df.columns if c.lower() in
         ("type", "instrument_type", "security_type", "comtypecode")), None)
    if type_col:
        stock_types = {"stock", "equity", "s", "cp", "commonstock", "common_stock"}
        raw = raw[df[type_col].astype(str).str.lower().isin(stock_types)]
    else:
        raw = raw[raw.str.match(r"^[A-Z]{3}$")]

    log.info(f"  → {len(raw)} HOSE stocks ready.")
    return raw.tolist()

# ── Sector mapping ────────────────────────────────────────────────────────────
def get_sector_map(tickers: list[str]) -> pd.DataFrame:
    from scripts.config import VINGROUP_TICKERS, VINGROUP_GROUP
    from vnstock import Vnstock
    
    listing = Vnstock().stock(source='VCI').listing
    base = pd.DataFrame({"ticker": tickers})
    base["sector"] = base["industry"] = "Unknown"

    raw = None
    try:
        raw = listing.symbols_by_industries()
        log.info(f"Sector data fetched: {len(raw)} rows")
    except Exception as exc:
        log.warning(f"Sector fetch failed: {exc}")

    if raw is None or raw.empty:
        base["group"] = "Unknown"
        mask = base["ticker"].isin(VINGROUP_TICKERS)
        base.loc[mask, "group"] = VINGROUP_GROUP
        return base

    cols = list(raw.columns)
    cl   = [c.lower() for c in cols]
    ticker_col   = next((cols[i] for i, c in enumerate(cl) if c in ("ticker", "symbol", "code")), None)
    sector_col   = next((cols[i] for i, c in enumerate(cl) if any(p in c for p in ("sector", "icbname", "groupname"))), None)
    industry_col = next((cols[i] for i, c in enumerate(cl) if any(p in c for p in ("industry", "nganh"))), None)

    if ticker_col is None:
        base["group"] = "Unknown"
        mask = base["ticker"].isin(VINGROUP_TICKERS)
        base.loc[mask, "group"] = VINGROUP_GROUP
        return base

    lookup = pd.DataFrame({"ticker": raw[ticker_col].astype(str).str.upper().str.strip()})
    if sector_col:   lookup["sector"]   = raw[sector_col].values
    if industry_col: lookup["industry"] = raw[industry_col].values
    lookup = lookup.drop_duplicates(subset="ticker", keep="first")

    base = base.drop(columns=["sector", "industry"], errors="ignore")
    base = base.merge(lookup, on="ticker", how="left")
    for col in ("sector", "industry"):
        if col not in base.columns: base[col] = "Unknown"
        base[col] = base[col].fillna("Unknown")

    base["group"] = base["sector"].copy()
    mask = base["ticker"].isin(VINGROUP_TICKERS)
    base.loc[mask, "group"] = VINGROUP_GROUP
    return base

# ── TTM EPS + latest BVPS extractor ──────────────────────────────────────────
def _extract_ttm(ratio_df: pd.DataFrame, ticker: str) -> dict:
    null = {"ticker": ticker, "eps_ttm": np.nan, "bvps": np.nan,
            "eps_method": "no_data", "fetched_date": str(date.today())}
    if ratio_df is None or ratio_df.empty:
        return null

    if "period" in ratio_df.columns:
        try:
            ratio_df = ratio_df.sort_values("period", ascending=False).reset_index(drop=True)
        except Exception:
            pass

    latest = ratio_df.iloc[0]

    # Flexible column matching for Vnstock 4.0
    eps_col = next((c for c in ratio_df.columns if 'eps' in c.lower() or 'earning_per_share' in c.lower()), None)
    bvps_col = next((c for c in ratio_df.columns if 'bvps' in c.lower() or 'book_value_per_share' in c.lower()), None)

    eps_ttm = pd.to_numeric(latest.get(eps_col, np.nan) if eps_col else np.nan, errors="coerce")
    bvps    = pd.to_numeric(latest.get(bvps_col, np.nan) if bvps_col else np.nan, errors="coerce")

    if pd.isna(eps_ttm) or eps_ttm <= 0:
        eps_method = "no_eps" if pd.isna(eps_ttm) else "negative_eps"
        eps_ttm    = np.nan
    else:
        eps_method = "trailing_eps"

    return {"ticker": ticker, "eps_ttm": eps_ttm, "bvps": bvps,
            "eps_method": eps_method, "fetched_date": str(date.today())}

def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "rate limit", "rate_limit", "throttl", "too many request", "exceeded"))

def fetch_all_fundamentals(tickers: list[str]) -> pd.DataFrame:
    from vnstock import Vnstock
    records = []
    n       = len(tickers)
    null    = lambda t: {"ticker": t, "eps_ttm": np.nan, "bvps": np.nan,
                         "eps_method": "failed", "fetched_date": str(date.today())}

    for i, ticker in enumerate(tickers, 1):
        success = False
        for attempt in range(1, FUND_MAX_RETRIES + 1):
            try:
                stock = Vnstock().stock(symbol=ticker, source='VCI')
                ratio_df = stock.finance.ratio(period="quarter", lang="en", dropna=True)
                records.append(_extract_ttm(ratio_df, ticker))
                success = True
                break
            except Exception as exc:
                if _is_rate_limit(exc):
                    wait = FUND_RETRY_WAIT * attempt
                    log.warning(f"  [{i}/{n}] {ticker} — rate limit (attempt {attempt}). Sleeping {wait}s...")
                    time.sleep(wait)
                else:
                    if attempt < FUND_MAX_RETRIES:
                        time.sleep(5)

        if not success:
            records.append(null(ticker))

        if i == 1 or i % 25 == 0:
            done = sum(1 for r in records if not np.isnan(r.get("eps_ttm", np.nan)))
            log.info(f"  Progress {i}/{n} | TTM EPS fetched: {done}")

        time.sleep(FUND_BATCH_SLEEP)
        if i % FUND_BATCH_EVERY == 0 and i < n:
            log.info(f"  Batch pause after {i} — sleeping {FUND_BATCH_PAUSE}s...")
            time.sleep(FUND_BATCH_PAUSE)

    df = pd.DataFrame(records).set_index("ticker")
    valid_eps  = df["eps_ttm"].notna().sum()
    valid_bvps = df["bvps"].notna().sum()
    log.info(f"Fundamentals complete: {len(df)} tickers | EPS valid: {valid_eps} | BVPS valid: {valid_bvps}")
    return df

def main():
    log.info("=== Weekly fundamentals refresh started ===")
    register_vnstock()
    tickers    = get_hose_tickers()
    sector_map = get_sector_map(tickers)
    funds      = fetch_all_fundamentals(tickers)

    merged = funds.reset_index().merge(
        sector_map[["ticker", "sector", "industry", "group"]],
        on="ticker", how="left")

    for col in ["sector", "industry", "group", "fetched_date", "eps_method"]:
        if col in merged.columns:
            merged[col] = merged[col].fillna("Unknown").astype(str)

    merged.to_parquet(FUND_FILE, index=False)
    log.info(f"Saved → {FUND_FILE}  ({len(merged)} rows)")
    log.info("=== Weekly fundamentals refresh complete ===")

if __name__ == "__main__":
    main()

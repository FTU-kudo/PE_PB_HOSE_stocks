"""
Weekly fundamentals refresh  (run every Sunday ~01:00 UTC via GitHub Actions).
For each HOSE ticker, fetches Finance.ratio(period='year') and extracts:
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
  equity structure differs — but Finance.ratio() handles this at the API level.
- Results are written to data/fundamentals.parquet (ticker as index).
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import (
    EXCHANGE, FUND_FILE, FUND_BATCH_SLEEP,
    PRICE_BOARD_BATCH, DATA_DIR, DAILY_DIR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Ensure directories ────────────────────────────────────────────────────────
for d in [DATA_DIR, DAILY_DIR, "docs"]:
    Path(d).mkdir(parents=True, exist_ok=True)


# ── vnstock authentication ────────────────────────────────────────────────────
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
# ── Ticker discovery ──────────────────────────────────────────────────────────
def get_hose_tickers() -> list[str]:
    """Return all HOSE equity tickers via vnstock Reference."""
    log.info("Fetching HOSE ticker universe...")
    from vnstock import Reference
    ref = Reference()
    try:
        df = ref.equity.list_by_exchange(exchange=EXCHANGE)
    except Exception:
        try:
            df = ref.equity.list_by_exchange(exchange="HSX")   # legacy alias
        except Exception as exc:
            log.error(f"list_by_exchange failed: {exc}")
            raise

    # Detect ticker column (handles 'ticker', 'symbol', 'code', etc.)
    ticker_col = next(
        (c for c in df.columns if c.lower() in ("ticker", "symbol", "code", "stock_code")),
        df.columns[0],
    )
    tickers = df[ticker_col].dropna().str.upper().str.strip().tolist()
    log.info(f"  → {len(tickers)} HOSE tickers found.")
    return tickers


# ── Sector mapping ────────────────────────────────────────────────────────────
def get_sector_map(tickers: list[str]) -> pd.DataFrame:
    """
    Build ticker → sector / industry / group mapping.
    Primary: Reference().equity.list_by_industry()  [VCI – ICB standard]
    Fallback: Reference().industry.sectors()         [KBS]
    Vingroup override applied last.
    """
    from scripts.config import VINGROUP_TICKERS, VINGROUP_GROUP
    from vnstock import Reference
    ref = Reference()
    base = pd.DataFrame({"ticker": tickers})

    def _try_vci():
        df = ref.equity.list_by_industry()
        cmap = {}
        for col in df.columns:
            cl = col.lower()
            if cl in ("ticker", "symbol", "code"):
                cmap[col] = "ticker"
            elif "industry" in cl or "nganh" in cl:
                cmap[col] = "industry"
            elif "sector" in cl or "linh_vuc" in cl:
                cmap[col] = "sector"
        df = df.rename(columns=cmap)
        df["ticker"] = df["ticker"].str.upper()
        return df

    def _try_kbs():
        df = ref.industry.sectors()
        cmap = {}
        for col in df.columns:
            cl = col.lower()
            if cl in ("ticker", "symbol", "code"):
                cmap[col] = "ticker"
            elif "industry" in cl or "sector" in cl or "nganh" in cl:
                cmap[col] = "sector"
        df = df.rename(columns=cmap)
        df["ticker"] = df["ticker"].str.upper()
        return df

    for fn, label in [(_try_vci, "VCI"), (_try_kbs, "KBS")]:
        try:
            ind = fn()
            merge_cols = [c for c in ("ticker", "sector", "industry") if c in ind.columns]
            base = base.merge(ind[merge_cols], on="ticker", how="left")
            log.info(f"Sector map loaded from {label}.")
            break
        except Exception as exc:
            log.warning(f"Sector map ({label}) failed: {exc}")

    for col in ("sector", "industry"):
        if col not in base.columns:
            base[col] = "Unknown"
    base["sector"]   = base["sector"].fillna("Unknown")
    base["industry"] = base["industry"].fillna("Unknown")

    # Special Vingroup group AFTER sector mapping
    base["group"] = base["sector"]
    mask = base["ticker"].isin(VINGROUP_TICKERS)
    base.loc[mask, "group"] = VINGROUP_GROUP
    return base


# ── Fundamentals fetch ────────────────────────────────────────────────────────
def _extract_eps_bvps(ratio_df: pd.DataFrame, ticker: str) -> dict:
    """
    From a Finance.ratio(period='year') DataFrame, extract the most recent:
      eps_annual  – EPS of the last complete fiscal year  (already period-specific)
      bvps        – Book Value Per Share (last annual)
    Returns dict with those two keys (NaN if not found).
    """
    null = {"ticker": ticker, "eps_annual": np.nan, "bvps": np.nan, "fetched_date": str(date.today())}
    if ratio_df is None or ratio_df.empty:
        return null

    # Detect EPS and BVPS columns (vnstock may return 'eps', 'EPS', 'bvps', etc.)
    cols_lower = {c.lower(): c for c in ratio_df.columns}
    eps_col  = cols_lower.get("eps",  cols_lower.get("earningspershare", None))
    bvps_col = cols_lower.get("bvps", cols_lower.get("bookvaluepershare",
               cols_lower.get("nav",  None)))

    row = ratio_df.iloc[-1]   # most recent period
    eps  = pd.to_numeric(row[eps_col],  errors="coerce") if eps_col  else np.nan
    bvps = pd.to_numeric(row[bvps_col], errors="coerce") if bvps_col else np.nan

    # Sanity: vnstock stores prices in raw VND (thousands) for some sources
    # EPS for Vietnamese stocks is typically 1,000–20,000 VND range; flag if implausible
    if not np.isnan(eps) and eps < 0:
        eps = np.nan   # loss-making → PE not meaningful
    return {"ticker": ticker, "eps_annual": eps, "bvps": bvps, "fetched_date": str(date.today())}


def fetch_all_fundamentals(tickers: list[str]) -> pd.DataFrame:
    """
    Batch-fetch Finance.ratio(period='year') for all tickers.
    Returns DataFrame indexed by ticker with eps_annual, bvps columns.
    """
    from vnstock import Finance
    records = []
    n = len(tickers)
    for i, ticker in enumerate(tickers, 1):
        if i % 25 == 0 or i == 1:
            log.info(f"  Fetching ratio {i}/{n}: {ticker}")
        try:
            fin = Finance(symbol=ticker, source="KBS")
            ratio_df = fin.ratio(period="year", lang="en")
            records.append(_extract_eps_bvps(ratio_df, ticker))
        except Exception as exc:
            log.debug(f"  {ticker}: ratio failed – {exc}")
            records.append({"ticker": ticker, "eps_annual": np.nan, "bvps": np.nan,
                            "fetched_date": str(date.today())})
        time.sleep(FUND_BATCH_SLEEP)

    df = pd.DataFrame(records).set_index("ticker")
    valid_eps  = df["eps_annual"].notna().sum()
    valid_bvps = df["bvps"].notna().sum()
    log.info(f"Fundamentals fetched: {len(df)} tickers | EPS valid: {valid_eps} | BVPS valid: {valid_bvps}")
    return df


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=== Weekly fundamentals refresh started ===")
    register_vnstock()

    tickers    = get_hose_tickers()
    sector_map = get_sector_map(tickers)

    fundamentals = fetch_all_fundamentals(tickers)

    # Merge sector info into fundamentals file
    merged = fundamentals.reset_index().merge(
        sector_map[["ticker", "sector", "industry", "group"]],
        on="ticker", how="left"
    )
    merged.to_parquet(FUND_FILE, index=False)
    log.info(f"Fundamentals saved → {FUND_FILE}  ({len(merged)} rows)")
    log.info("=== Weekly fundamentals refresh complete ===")


if __name__ == "__main__":
    main()

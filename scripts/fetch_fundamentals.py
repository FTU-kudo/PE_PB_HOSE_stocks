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
    """
    Return all HOSE equity tickers via vnstock Reference.

    Tries multiple calling conventions in order because vnstock v4
    changed list_by_exchange() to NOT accept 'exchange' as a keyword arg:
      1. positional: list_by_exchange("HOSE")
      2. positional: list_by_exchange("HSX")      ← legacy alias
      3. no-arg:     list_by_exchange()  → filter result by exchange column
      4. fallback:   listing.all_symbols() → filter by exchange column
    """
    log.info("Fetching HOSE ticker universe...")
    from vnstock import Reference
    ref = Reference()

    df = None
    strategies = [
        ("positional HOSE",    lambda: ref.equity.list_by_exchange("HOSE")),
        ("positional HSX",     lambda: ref.equity.list_by_exchange("HSX")),
        ("no-arg all",         lambda: ref.equity.list_by_exchange()),
        ("listing.all_symbols",lambda: ref.listing.all_symbols()),
    ]

    for label, fn in strategies:
        try:
            result = fn()
            if result is not None and not result.empty:
                df = result
                log.info(f"  list_by_exchange strategy '{label}' succeeded ({len(df)} rows).")
                break
        except Exception as exc:
            log.debug(f"  Strategy '{label}' failed: {exc}")

    if df is None or df.empty:
        raise RuntimeError(
            "All strategies to fetch HOSE tickers failed. "
            "Check vnstock version and API availability."
        )

    # If we fetched ALL exchanges (no-arg / all_symbols), filter to HOSE only
    exchange_col = next(
        (c for c in df.columns
         if c.lower() in ("exchange", "san", "listing_on", "comgroupcode", "market")),
        None,
    )
    if exchange_col:
        before = len(df)
        df = df[df[exchange_col].str.upper().isin(["HOSE", "HSX"])]
        log.info(f"  Filtered {before} → {len(df)} HOSE tickers via column '{exchange_col}'.")

    ticker_col = next(
        (c for c in df.columns
         if c.lower() in ("ticker", "symbol", "code", "stock_code")),
        df.columns[0],
    )
    tickers = df[ticker_col].dropna().str.upper().str.strip().tolist()
    log.info(f"  → {len(tickers)} HOSE tickers ready.")
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


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect rate-limit / throttle errors from vnstock / underlying HTTP layer."""
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "rate limit", "rate_limit",
                                  "throttl", "too many request", "exceeded"))


def fetch_all_fundamentals(tickers: list[str]) -> pd.DataFrame:
    """
    Batch-fetch Finance.ratio(period='year') for all tickers with:
      - Exponential backoff on rate-limit errors (90 s × attempt)
      - Base sleep of FUND_BATCH_SLEEP seconds between every call
      - Longer pause of FUND_BATCH_PAUSE seconds every FUND_BATCH_EVERY tickers
        so the API rate-limit window can reset

    Effective throughput (defaults):
      15 tickers × 4 s sleep = 60 s + 45 s batch pause = 105 s per batch
      → ~8.6 tickers/min  →  ~46 min total for 400 tickers  (well within 2 h timeout)
      Rate: ~8.6 req/min (safely under Guest-tier 20 req/min even if
            Finance.ratio() makes 2 internal HTTP calls)
    """
    from vnstock import Finance
    records = []
    n       = len(tickers)
    null    = lambda t: {"ticker": t, "eps_annual": np.nan, "bvps": np.nan,
                         "fetched_date": str(date.today())}

    for i, ticker in enumerate(tickers, 1):

        # ── Per-ticker fetch with retry ───────────────────────────────────────
        success = False
        for attempt in range(1, FUND_MAX_RETRIES + 1):
            try:
                fin      = Finance(symbol=ticker, source="KBS")
                ratio_df = fin.ratio(period="year", lang="en")
                records.append(_extract_eps_bvps(ratio_df, ticker))
                success  = True
                break

            except Exception as exc:
                if _is_rate_limit_error(exc):
                    wait = FUND_RETRY_WAIT * attempt          # 90 s, 180 s, 270 s
                    log.warning(
                        f"  [{i}/{n}] {ticker} — rate limit hit "
                        f"(attempt {attempt}/{FUND_MAX_RETRIES}). "
                        f"Sleeping {wait} s before retry..."
                    )
                    time.sleep(wait)
                else:
                    # Non-rate-limit error: short wait then retry
                    log.debug(f"  [{i}/{n}] {ticker} — attempt {attempt} failed: {exc}")
                    if attempt < FUND_MAX_RETRIES:
                        time.sleep(5)

        if not success:
            log.warning(f"  [{i}/{n}] {ticker} — gave up after {FUND_MAX_RETRIES} attempts.")
            records.append(null(ticker))

        # ── Progress log every 25 tickers ────────────────────────────────────
        if i == 1 or i % 25 == 0:
            done  = sum(1 for r in records if not np.isnan(r.get("eps_annual", np.nan)))
            log.info(f"  Progress {i}/{n} | EPS fetched so far: {done}")

        # ── Base inter-call sleep ─────────────────────────────────────────────
        time.sleep(FUND_BATCH_SLEEP)

        # ── Batch-level pause every FUND_BATCH_EVERY tickers ─────────────────
        if i % FUND_BATCH_EVERY == 0 and i < n:
            log.info(
                f"  Batch pause after {i} tickers "
                f"— sleeping {FUND_BATCH_PAUSE} s to reset rate-limit window..."
            )
            time.sleep(FUND_BATCH_PAUSE)

    df         = pd.DataFrame(records).set_index("ticker")
    valid_eps  = df["eps_annual"].notna().sum()
    valid_bvps = df["bvps"].notna().sum()
    log.info(
        f"Fundamentals complete: {len(df)} tickers | "
        f"EPS valid: {valid_eps} ({valid_eps/len(df)*100:.0f}%) | "
        f"BVPS valid: {valid_bvps} ({valid_bvps/len(df)*100:.0f}%)"
    )
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

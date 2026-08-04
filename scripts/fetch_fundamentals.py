"""
Weekly fundamentals refresh — fetches TTM EPS and latest quarterly BVPS
for all HOSE stocks via vnstock Fundamental API.

TTM EPS formula (VAS-compatible)
---------------------------------
VAS quarterly income statements are year-to-date CUMULATIVE:
  Q1 IS = Jan–Mar    Q2 IS = Jan–Jun    Q3 IS = Jan–Sep    Q4 IS = Jan–Dec

Because of this, we CANNOT simply sum 4 quarterly EPS rows.
Instead we use:

  TTM = Annual_EPS_(Y-1)  +  YTD_EPS_MostRecentQuarter
        -  YTD_EPS_SameQuarter_(Y-1)

Example (latest filed = Q1-2025):
  TTM = Annual_2024 + Q1-2025_cum - Q1-2024_cum

Special cases:
  • If latest quarter is Q4 → Q4 cumulative = full year → use directly.
  • If same-quarter prior-year data is missing → fall back to Annual_(Y-1).
  • If no quarterly data at all → fall back to most recent annual.

BVPS
-----
Balance sheet = point-in-time, not a flow.
"TTM BVPS" is simply the most recent *quarterly* BVPS value.
This is more current than annual BVPS which can be 6–18 months old.
"""

import os, sys, re, time, logging, warnings
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


# ── Auth ──────────────────────────────────────────────────────────────────────
def register_vnstock() -> None:
    api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
    if not api_key:
        log.warning("VNSTOCK_API_KEY not set — Guest mode (20 req/min).")
        return
    try:
        from vnstock import set_api_key
        set_api_key(api_key)
        log.info("vnstock registered via set_api_key.")
        return
    except (ImportError, AttributeError):
        pass
    for module, func in [
        ("vnstock",             "register_user"),
        ("vnstock.common.user", "register_user"),
        ("vnstock",             "init_user"),
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
    log.warning("Could not register API key. Guest mode.")


# ── Ticker discovery ──────────────────────────────────────────────────────────
def get_hose_tickers() -> list[str]:
    log.info("Fetching HOSE ticker universe...")
    from vnstock import Reference
    ref = Reference()
    df  = None
    for label, fn in [
        ("positional HOSE",     lambda: ref.equity.list_by_exchange("HOSE")),
        ("positional HSX",      lambda: ref.equity.list_by_exchange("HSX")),
        ("no-arg all",          lambda: ref.equity.list_by_exchange()),
        ("listing.all_symbols", lambda: ref.listing.all_symbols()),
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

    exch_col = next(
        (c for c in df.columns if c.lower() in
         ("exchange", "san", "listing_on", "comgroupcode", "market")), None)
    if exch_col:
        before = len(df)
        df = df[df[exch_col].str.upper().isin(["HOSE", "HSX"])]
        log.info(f"  Exchange filter: {before} → {len(df)}.")

    ticker_col = next(
        (c for c in df.columns if c.lower() in
         ("ticker", "symbol", "code", "stock_code")), df.columns[0])
    raw = df[ticker_col].dropna().str.upper().str.strip()

    type_col = next(
        (c for c in df.columns if c.lower() in
         ("type", "instrument_type", "security_type", "comtypecode")), None)
    if type_col:
        stock_types = {"stock", "equity", "s", "cp", "commonstock", "common_stock"}
        raw = raw[df[type_col].str.lower().isin(stock_types)]
        log.info(f"  Type filter: → {len(raw)} stocks.")
    else:
        before = len(raw)
        raw = raw[raw.str.match(r"^[A-Z]{3}$")]
        log.info(f"  Regex filter: {before} → {len(raw)} stocks.")

    log.info(f"  → {len(raw)} HOSE stocks ready.")
    return raw.tolist()


# ── Sector mapping ────────────────────────────────────────────────────────────
def get_sector_map(tickers: list[str]) -> pd.DataFrame:
    from scripts.config import VINGROUP_TICKERS, VINGROUP_GROUP
    from vnstock import Reference
    ref  = Reference()
    base = pd.DataFrame({"ticker": tickers})
    base["sector"] = base["industry"] = "Unknown"

    raw = None
    for label, fn in [
        ("VCI list_by_industry", lambda: ref.equity.list_by_industry()),
        ("KBS sectors",          lambda: ref.industry.sectors()),
    ]:
        try:
            r = fn()
            if r is not None and not r.empty:
                raw = r
                log.info(f"Sector data from {label}: {len(raw)} rows | cols: {list(raw.columns)}")
                break
        except Exception as exc:
            log.warning(f"Sector '{label}' failed: {exc}")

    if raw is None:
        base["group"] = "Unknown"
        mask = base["ticker"].isin(VINGROUP_TICKERS)
        base.loc[mask, "group"] = VINGROUP_GROUP
        return base

    cols = list(raw.columns)
    cl   = [c.lower() for c in cols]
    ticker_col   = next((cols[i] for i, c in enumerate(cl) if c in
                         ("ticker", "symbol", "code", "stockcode")), None)
    sector_col   = next((cols[i] for i, c in enumerate(cl) if any(p in c for p in
                         ("sector", "linh_vuc", "icbname", "groupname"))), None)
    industry_col = next((cols[i] for i, c in enumerate(cl) if any(p in c for p in
                         ("industry", "nganh", "industryname"))), None)
    log.info(f"  Detected → ticker='{ticker_col}' | sector='{sector_col}' | industry='{industry_col}'")

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
    log.info(f"Sector map done. Top: {base['group'].value_counts().head(4).to_dict()}")
    return base


# ── Period-column parser ──────────────────────────────────────────────────────
def _parse_period_cols(columns) -> list[tuple]:
    """
    Returns sorted list of (col_name, year, quarter) tuples.
    quarter=0  → annual row
    quarter=1–4 → Q1–Q4 cumulative YTD

    Handles column formats:
      '2024'      → annual
      '2024/1'    → Q1-2024 cumulative
      '2024/Q1'   → same
      'Q1/2024'   → same
      '20241'     → same (5-digit)
    """
    result = []
    for col in columns:
        s = str(col).strip()
        if re.fullmatch(r"\d{4}", s):
            result.append((col, int(s), 0))
        elif m := re.fullmatch(r"(\d{4})[/\-_]Q?(\d)", s):
            result.append((col, int(m[1]), int(m[2])))
        elif m := re.fullmatch(r"Q(\d)[/\-_](\d{4})", s):
            result.append((col, int(m[2]), int(m[1])))
        elif re.fullmatch(r"\d{5}", s) and 1 <= int(s[-1]) <= 4:
            result.append((col, int(s[:4]), int(s[-1])))
    return sorted(result, key=lambda x: (x[1], x[2]))


# ── TTM EPS + latest BVPS extractor ──────────────────────────────────────────
def _extract_ttm(ratio_df: pd.DataFrame, ticker: str) -> dict:
    """
    From a quarterly ratio DataFrame (orient='report'), extract:
      eps_ttm  — TTM EPS via VAS cumulative formula
      bvps     — most recent quarterly BVPS

    Returns dict with eps_ttm, bvps, eps_method (for auditability).
    """
    null = {"ticker": ticker, "eps_ttm": np.nan, "bvps": np.nan,
            "eps_method": "no_data", "fetched_date": str(date.today())}
    if ratio_df is None or ratio_df.empty:
        return null

    # ── Detect metric-name column ─────────────────────────────────────────────
    name_col = next(
        (c for c in ("item_en", "item", "name", "metric") if c in ratio_df.columns),
        None)
    if name_col is None:
        return null

    # ── Parse all time-period columns ─────────────────────────────────────────
    periods   = _parse_period_cols(ratio_df.columns)
    annuals   = [(c, y, q) for c, y, q in periods if q == 0]
    quarters  = [(c, y, q) for c, y, q in periods if q > 0]

    def val(row, col):
        return pd.to_numeric(row[col], errors="coerce") if col in row.index else np.nan

    # ── Find EPS and BVPS rows ────────────────────────────────────────────────
    eps_row  = ratio_df[ratio_df[name_col].str.contains(
                   r"EPS|Earnings Per Share", case=False, na=False)]
    bvps_row = ratio_df[ratio_df[name_col].str.contains(
                   r"BVPS|Book Value Per Share", case=False, na=False)]

    # ── BVPS: most recent quarter, fall back to most recent annual ────────────
    bvps = np.nan
    if not bvps_row.empty:
        row = bvps_row.iloc[0]
        for col, y, q in reversed(quarters + annuals):   # prefer newer / quarterly
            v = val(row, col)
            if not np.isnan(v) and v > 0:
                bvps = v
                break

    # ── TTM EPS ───────────────────────────────────────────────────────────────
    eps_ttm    = np.nan
    eps_method = "no_data"

    if eps_row.empty:
        return {**null, "bvps": bvps}

    row = eps_row.iloc[0]

    if quarters:
        mr_col, mr_year, mr_q = quarters[-1]       # most recent quarter filed
        mr_eps = val(row, mr_col)

        if mr_q == 4:
            # Q4 cumulative = full year — use directly
            if not np.isnan(mr_eps) and mr_eps > 0:
                eps_ttm, eps_method = mr_eps, "q4_annual"

        elif not np.isnan(mr_eps):
            # Find same quarter prior year (YTD comparison)
            pq_col = next(
                (c for c, y, q in quarters if y == mr_year - 1 and q == mr_q),
                None)
            # Find last complete annual (prior year preferred)
            ann_col = next(
                (c for c, y, q in reversed(annuals) if y == mr_year - 1),
                next((c for c, y, q in reversed(annuals) if y < mr_year), None))

            if pq_col and ann_col:
                pq_eps  = val(row, pq_col)
                ann_eps = val(row, ann_col)
                if not np.isnan(pq_eps) and not np.isnan(ann_eps):
                    ttm = ann_eps + mr_eps - pq_eps
                    if ttm > 0:
                        eps_ttm, eps_method = ttm, "ttm_formula"
                    # else: negative TTM → loss-making, leave as NaN

            elif ann_col:
                # Prior-year same-quarter missing → fall back to annual
                ann_eps = val(row, ann_col)
                if not np.isnan(ann_eps) and ann_eps > 0:
                    eps_ttm, eps_method = ann_eps, "annual_fallback"

    if np.isnan(eps_ttm) and annuals:
        # No usable quarterly data → use most recent annual
        ann_eps = val(row, annuals[-1][0])
        if not np.isnan(ann_eps) and ann_eps > 0:
            eps_ttm, eps_method = ann_eps, "annual_only"

    return {"ticker": ticker, "eps_ttm": eps_ttm, "bvps": bvps,
            "eps_method": eps_method, "fetched_date": str(date.today())}


# ── Rate-limit error detector ─────────────────────────────────────────────────
def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "rate limit", "rate_limit",
                                  "throttl", "too many request", "exceeded"))


# ── Main fundamentals fetch ───────────────────────────────────────────────────
def fetch_all_fundamentals(tickers: list[str]) -> pd.DataFrame:
    """
    Calls Fundamental.equity(ticker).ratio(period='quarter') for each ticker.
    Extracts TTM EPS and latest quarterly BVPS.
    """
    from vnstock import Fundamental
    fun     = Fundamental()
    records = []
    n       = len(tickers)
    null    = lambda t: {"ticker": t, "eps_ttm": np.nan, "bvps": np.nan,
                         "eps_method": "failed", "fetched_date": str(date.today())}

    for i, ticker in enumerate(tickers, 1):
        success = False
        for attempt in range(1, FUND_MAX_RETRIES + 1):
            try:
                ratio_df = fun.equity(ticker).ratio(
                    period="quarter", orient="report", lang="en")
                records.append(_extract_ttm(ratio_df, ticker))
                success = True
                break
            except Exception as exc:
                if _is_rate_limit(exc):
                    wait = FUND_RETRY_WAIT * attempt
                    log.warning(f"  [{i}/{n}] {ticker} — rate limit "
                                f"(attempt {attempt}). Sleeping {wait}s...")
                    time.sleep(wait)
                else:
                    log.debug(f"  [{i}/{n}] {ticker} — attempt {attempt}: {exc}")
                    if attempt < FUND_MAX_RETRIES:
                        time.sleep(5)

        if not success:
            log.warning(f"  [{i}/{n}] {ticker} — gave up.")
            records.append(null(ticker))

        if i == 1 or i % 25 == 0:
            done = sum(1 for r in records if not np.isnan(r.get("eps_ttm", np.nan)))
            log.info(f"  Progress {i}/{n} | TTM EPS fetched: {done}")

        time.sleep(FUND_BATCH_SLEEP)
        if i % FUND_BATCH_EVERY == 0 and i < n:
            log.info(f"  Batch pause after {i} — sleeping {FUND_BATCH_PAUSE}s...")
            time.sleep(FUND_BATCH_PAUSE)

    df         = pd.DataFrame(records).set_index("ticker")
    valid_eps  = df["eps_ttm"].notna().sum()
    valid_bvps = df["bvps"].notna().sum()

    # Log breakdown of eps_method
    if "eps_method" in df.columns:
        log.info(f"EPS method breakdown:\n{df['eps_method'].value_counts().to_string()}")

    log.info(f"Fundamentals complete: {len(df)} tickers | "
             f"TTM EPS valid: {valid_eps} ({valid_eps/len(df)*100:.0f}%) | "
             f"BVPS valid: {valid_bvps} ({valid_bvps/len(df)*100:.0f}%)")
    return df


# ── Main ──────────────────────────────────────────────────────────────────────
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

    # ── Missing-data report ───────────────────────────────────────────────────
    missing_eps  = sorted(merged[merged["eps_ttm"].isna()]["ticker"].tolist())
    missing_bvps = sorted(merged[merged["bvps"].isna()]["ticker"].tolist())

    if missing_eps:
        log.warning(f"MISSING TTM EPS ({len(missing_eps)}): {', '.join(missing_eps)}")
    else:
        log.info("All tickers have valid TTM EPS ✓")

    if missing_bvps:
        log.warning(f"MISSING BVPS ({len(missing_bvps)}): {', '.join(missing_bvps)}")
    else:
        log.info("All tickers have valid BVPS ✓")

    log.info("=== Weekly fundamentals refresh complete ===")


if __name__ == "__main__":
    main()

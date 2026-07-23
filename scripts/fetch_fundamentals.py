"""
Weekly fundamentals refresh  (run every Sunday ~01:00 UTC via GitHub Actions).
For each HOSE ticker fetches:
  - eps_ttm : TTM EPS = sum of net profit (isa22) from the 4 most recent
              standalone quarters via Finance.income_statement(period='quarter',
              source='VCI'), divided by shares outstanding.
              Fallback: KBS Finance.ratio(period='quarter') trailing_eps field.
  - bvps    : Book Value Per Share from KBS Finance.ratio(period='year').

Design notes
------------
- VCI income_statement(period='quarter') returns standalone quarter values
  (already deaccumulated from VAS YTD cumulative), making TTM summation
  straightforward: TTM = Q(t) + Q(t-1) + Q(t-2) + Q(t-3).
- VCI updates faster than KBS after each quarterly BCTC publication, so
  TTM values stay current within days of a new report.
- BVPS comes from the most recent KBS annual ratio row (balance sheet changes
  are slower; annual is sufficient).
- Results are written to data/fundamentals.parquet.
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
    from scripts.config import EXCHANGE
    ref = Reference()
    try:
        df = ref.equity.list_by_exchange()
    except TypeError:
        try:
            df = ref.equity.list_by_exchange(exchange=EXCHANGE)
        except Exception:
            df = ref.equity.list_by_exchange(exchange="HSX")

    ex_col = next(
        (c for c in df.columns if c.lower() in ("exchange", "board", "san", "market")),
        None,
    )
    if ex_col is not None:
        df = df[df[ex_col].astype(str).str.upper().isin(["HOSE", "HSX", "XSTC"])]

    # Detect ticker column (handles 'ticker', 'symbol', 'code', etc.)
    ticker_col = next(
        (c for c in df.columns if c.lower() in ("ticker", "symbol", "code", "stock_code")),
        df.columns[0],
    )
    raw_tickers = df[ticker_col].dropna().astype(str).str.upper().str.strip().tolist()
    # Exclude covered warrants (len 8, e.g. CHPG2616) and ETFs/open funds (e.g. FUEVN100)
    tickers = [t for t in raw_tickers if len(t) == 3]
    log.info(f"  -> {len(tickers)} HOSE stocks found (filtered out {len(raw_tickers) - len(tickers)} warrants/ETFs).")
    return tickers


# ── Sector mapping ────────────────────────────────────────────────────────────
def get_sector_map(tickers: list[str]) -> pd.DataFrame:
    """
    Build ticker → sector / industry / group mapping.
    Primary: Reference().equity.list_by_industry()
    Vingroup override applied last.
    """
    from scripts.config import VINGROUP_TICKERS, VINGROUP_GROUP
    from vnstock import Reference
    ref = Reference()
    base = pd.DataFrame({"ticker": tickers})

    try:
        df = ref.equity.list_by_industry()
        df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
        df = df[df["symbol"].isin(tickers)]

        if "icb_level" in df.columns and "icb_name" in df.columns:
            sec_df = df[df["icb_level"].astype(str).isin(["1", "2"])].drop_duplicates(subset=["symbol"])
            ind_df = df[df["icb_level"].astype(str).isin(["3", "4"])].drop_duplicates(subset=["symbol"])
            if sec_df.empty:
                sec_df = df.drop_duplicates(subset=["symbol"])
            if ind_df.empty:
                ind_df = df.drop_duplicates(subset=["symbol"], keep="last")

            sec_map = sec_df.set_index("symbol")["icb_name"].to_dict()
            ind_map = ind_df.set_index("symbol")["icb_name"].to_dict()

            base["sector"] = base["ticker"].map(sec_map).fillna("Unknown")
            base["industry"] = base["ticker"].map(ind_map).fillna("Unknown")
        else:
            df_unique = df.drop_duplicates(subset=["symbol"])
            sec_col = next((c for c in df.columns if any(k in c.lower() for k in ["sector", "nganh", "icb_name"])), None)
            base["sector"] = base["ticker"].map(df_unique.set_index("symbol")[sec_col]).fillna("Unknown") if sec_col else "Unknown"
            base["industry"] = base["sector"]
        log.info("Sector map loaded successfully.")
    except Exception as exc:
        log.warning(f"Sector map failed: {exc}")
        base["sector"] = "Unknown"
        base["industry"] = "Unknown"

    # Separate major independent sectors
    base["group"] = base["sector"]

    mask_bds = base["industry"].astype(str).str.lower().str.contains("bất động|real estate")
    base.loc[mask_bds, "sector"] = "Bất động sản"
    base.loc[mask_bds, "group"]  = "Bất động sản"

    mask_xd = base["industry"].astype(str).str.lower().str.contains("xây dựng và vật liệu|construction & materials|construction and materials")
    base.loc[mask_xd, "sector"] = "Xây dựng và Vật liệu"
    base.loc[mask_xd, "group"]  = "Xây dựng và Vật liệu"

    mask_hc = base["industry"].astype(str).str.lower().str.contains("hóa chất|chemical")
    base.loc[mask_hc, "sector"] = "Hóa chất"
    base.loc[mask_hc, "group"]  = "Hóa chất"

    mask_tp = base["industry"].astype(str).str.lower().str.contains("sản xuất thực phẩm|food producer")
    base.loc[mask_tp, "sector"] = "Sản xuất thực phẩm"
    base.loc[mask_tp, "group"]  = "Sản xuất thực phẩm"

    mask_vin = base["ticker"].isin(VINGROUP_TICKERS)
    base.loc[mask_vin, "group"] = VINGROUP_GROUP
    return base.drop_duplicates(subset=["ticker"])


# ── Fundamentals fetch ────────────────────────────────────────────────────────
def _extract_bvps(ratio_df: pd.DataFrame, ticker: str) -> float:
    """
    From a Finance.ratio(period='year') DataFrame, extract the most recent
    BVPS (Book Value Per Share). Returns NaN if not found.
    """
    if ratio_df is None or ratio_df.empty:
        return np.nan

    item_col = next((c for c in ratio_df.columns if c.lower() in ("item", "chi_tieu", "name", "metric")), None)
    meta_lower = {"item", "item_id", "id", "symbol", "ticker", "item_en", "period"}
    val_cols = [c for c in ratio_df.columns if c.lower() not in meta_lower]

    if item_col is not None:
        for _, row in ratio_df.iterrows():
            item_str = str(row.get("item", "")).lower()
            item_id  = str(row.get("item_id", "")).lower()
            is_bvps  = ("book_value_per_share" in item_id or "bvps" in item_id
                        or "bvps" in item_str or "giá trị sổ sách" in item_str)
            if not is_bvps:
                continue
            for vc in val_cols:
                v = pd.to_numeric(row[vc], errors="coerce")
                if pd.notna(v) and v != 0:
                    return float(v)
    else:
        cols_lower = {c.lower(): c for c in ratio_df.columns}
        bvps_col = next((cols_lower[k] for k in cols_lower
                         if "bvps" in k or "bookvalue" in k or "nav" in k), None)
        if bvps_col:
            for idx in range(len(ratio_df) - 1, -1, -1):
                v = pd.to_numeric(ratio_df.iloc[idx][bvps_col], errors="coerce")
                if pd.notna(v) and v != 0:
                    return float(v)
    return np.nan


def _compute_ttm_eps(ticker: str, shares: float) -> float:
    """
    Compute TTM EPS from VCI income_statement(period='quarter').

    VCI returns standalone-quarter profits (already deaccumulated from VAS
    YTD cumulative), so TTM = sum of the 4 most recent 'isa22' values
    (net profit attributable to parent company shareholders), divided by shares.

    Fallback: KBS Finance.ratio(period='quarter') trailing_eps field.
    Returns NaN if both sources fail or profit is negative / zero.
    """
    from vnstock import Finance

    if not (pd.notna(shares) and shares > 0):
        return np.nan

    # ── Primary: VCI income_statement quarterly ───────────────────────────────
    try:
        fin_vci = Finance(symbol=ticker, source="VCI")
        is_df = fin_vci.income_statement(period="quarter", lang="en")
        if is_df is not None and not is_df.empty:
            meta_cols = {"item", "item_en", "item_id", "period"}
            val_cols  = [c for c in is_df.columns if c not in meta_cols]

            # isa22 = net profit attributable to parent company shareholders
            pat = is_df[is_df["item_id"].astype(str).str.lower() == "isa22"]
            if not pat.empty and len(val_cols) >= 4:
                recent_4 = val_cols[:4]   # columns are already sorted newest-first
                vals = pd.to_numeric(pat[recent_4].iloc[0], errors="coerce")
                if vals.notna().sum() == 4:
                    ttm_profit = float(vals.sum())
                    if ttm_profit > 0:
                        eps = ttm_profit / shares
                        log.debug(f"  {ticker}: VCI TTM EPS = {eps:,.0f} (profit {ttm_profit/1e9:.1f}B)")
                        return eps
    except Exception as exc:
        log.debug(f"  {ticker}: VCI income_statement failed – {exc}")

    # ── Fallback: KBS ratio quarter trailing_eps ──────────────────────────────
    try:
        fin_kbs = Finance(symbol=ticker, source="KBS")
        r = fin_kbs.ratio(period="quarter", lang="en")
        if r is not None and not r.empty:
            meta_cols = {"item", "item_en", "item_id", "period"}
            val_cols  = [c for c in r.columns if c not in meta_cols]
            trail = r[r["item_id"].astype(str).str.lower() == "trailing_eps"]
            if not trail.empty and val_cols:
                v = pd.to_numeric(trail[val_cols[0]].iloc[0], errors="coerce")
                if pd.notna(v) and v > 0:
                    log.debug(f"  {ticker}: KBS trailing_eps fallback = {v:,.0f}")
                    return float(v)
    except Exception as exc:
        log.debug(f"  {ticker}: KBS trailing_eps fallback failed – {exc}")

    return np.nan


import concurrent.futures

def fetch_all_fundamentals(tickers: list[str]) -> pd.DataFrame:
    """
    Fetch BVPS, Shares, and TTM EPS for all tickers concurrently.
    """
    records = []
    n = len(tickers)
    
    def fetch_single(args):
        i, ticker = args
        if i % 25 == 0 or i == 1:
            log.info(f"  Fetching fundamentals {i}/{n}: {ticker}")
        rec = {"ticker": ticker, "eps_ttm": np.nan, "bvps": np.nan, "shares": np.nan,
               "fetched_date": str(date.today())}

        # ── 1. BVPS from KBS annual ratio ─────────────────────────────────────
        try:
            from vnstock import Finance, Company
            fin = Finance(symbol=ticker, source="KBS")
            ratio_df = fin.ratio(period="year", lang="en")
            rec["bvps"] = _extract_bvps(ratio_df, ticker)
        except Exception as exc:
            log.debug(f"  {ticker}: KBS ratio(year) failed – {exc}")

        # ── 2. Shares outstanding ──────────────────────────────────────────────
        try:
            for src in ["KBS", "VCI"]:
                try:
                    from vnstock import Company
                    c = Company(symbol=ticker, source=src)
                    ov = c.overview()
                    if ov is not None and not ov.empty and "outstanding_shares" in ov.columns:
                        val = pd.to_numeric(ov.iloc[0]["outstanding_shares"], errors="coerce")
                        if pd.notna(val) and val > 0:
                            rec["shares"] = float(val)
                            break
                except Exception:
                    continue
        except Exception as exc:
            log.debug(f"  {ticker}: shares failed – {exc}")

        # ── 3. TTM EPS (VCI primary, KBS fallback) ─────────────────────────────
        rec["eps_ttm"] = _compute_ttm_eps(ticker, rec["shares"])
        
        # Small sleep to prevent rate limiting inside the thread
        time.sleep(FUND_BATCH_SLEEP / 5.0)
        return rec

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        args = [(i, ticker) for i, ticker in enumerate(tickers, 1)]
        results = executor.map(fetch_single, args)
        for rec in results:
            records.append(rec)

    df = pd.DataFrame(records).set_index("ticker")
    valid_eps  = df["eps_ttm"].notna().sum()
    valid_bvps = df["bvps"].notna().sum()
    valid_sh   = df["shares"].notna().sum()
    log.info(f"Fundamentals fetched: {len(df)} tickers | TTM EPS valid: {valid_eps} | BVPS valid: {valid_bvps} | Shares valid: {valid_sh}")
    return df


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    log.info("=== Weekly fundamentals refresh (TTM EPS) started ===")
    register_vnstock()

    tickers    = get_hose_tickers()
    sector_map = get_sector_map(tickers)

    fundamentals = fetch_all_fundamentals(tickers)

    # Merge sector info into fundamentals file
    merged = fundamentals.reset_index().merge(
        sector_map[["ticker", "sector", "industry", "group"]],
        on="ticker", how="left"
    ).drop_duplicates(subset=["ticker"])
    merged.to_parquet(FUND_FILE, index=False)
    log.info(f"Fundamentals saved -> {FUND_FILE}  ({len(merged)} rows)")
    log.info("=== Weekly fundamentals refresh (TTM EPS) complete ===")


if __name__ == "__main__":
    main()

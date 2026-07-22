"""
One-off / helper script to fetch `outstanding_shares` (`shares`) from vnstock Company.overview()
for all tickers in data/fundamentals.parquet and add/update the `shares` column.
"""

import sys
import time
import logging
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import FUND_FILE, FUND_BATCH_SLEEP
from scripts.fetch_fundamentals import register_vnstock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def fetch_shares_for_ticker(ticker: str) -> float:
    from vnstock import Company
    for source in ["KBS", "VCI"]:
        try:
            c = Company(symbol=ticker, source=source)
            ov = c.overview()
            if ov is not None and not ov.empty and "outstanding_shares" in ov.columns:
                val = pd.to_numeric(ov.iloc[0]["outstanding_shares"], errors="coerce")
                if pd.notna(val) and val > 0:
                    return float(val)
        except Exception:
            continue
    return np.nan


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    log.info("=== Fetching outstanding shares for fundamentals ===")
    register_vnstock()

    if not Path(FUND_FILE).exists():
        log.error(f"{FUND_FILE} does not exist. Run fetch_fundamentals.py first.")
        sys.exit(1)

    df = pd.read_parquet(FUND_FILE)
    if "ticker" in df.columns:
        df = df.set_index("ticker")

    tickers = df.index.unique().tolist()
    n = len(tickers)
    log.info(f"Loaded {n} tickers from {FUND_FILE}.")

    shares_map = {}
    # If shares column already exists and has some values, preserve them or re-fetch
    if "shares" in df.columns:
        shares_map = df["shares"].dropna().to_dict()

    for i, ticker in enumerate(tickers, 1):
        if ticker in shares_map and pd.notna(shares_map[ticker]) and shares_map[ticker] > 0:
            continue
        if i % 25 == 0 or i == 1:
            log.info(f"  Fetching shares {i}/{n}: {ticker}")
        val = fetch_shares_for_ticker(ticker)
        if pd.notna(val):
            shares_map[ticker] = val
        else:
            log.debug(f"  {ticker}: shares not found")
        time.sleep(FUND_BATCH_SLEEP)

    df["shares"] = pd.Series(shares_map)
    valid_shares = df["shares"].notna().sum()
    log.info(f"Finished. Valid shares count: {valid_shares}/{n}")

    # Save back to fundamentals.parquet
    df.to_parquet(FUND_FILE)
    log.info(f"Saved updated {FUND_FILE} with shares column.")


if __name__ == "__main__":
    main()

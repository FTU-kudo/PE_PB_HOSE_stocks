"""
Central configuration for VN HOSE P/E & P/B analysis pipeline.
All constants live here so callers never have magic literals.
"""

# ── Vingroup ecosystem (special group overrides sector) ──────────────────────
VINGROUP_TICKERS  = ["VIC", "VHM", "VRE", "VPL"]
VINGROUP_GROUP    = "Vingroup Ecosystem"

# ── Exchange filter ───────────────────────────────────────────────────────────
EXCHANGE = "HOSE"

# ── Outlier guard-rails (filter before aggregation, keep as NaN) ─────────────
PE_MIN, PE_MAX = 0.5, 150     # Negative / loss-making → NaN; extreme outliers → NaN
PB_MIN, PB_MAX = 0.1, 30

# ── API call parameters ───────────────────────────────────────────────────────
PRICE_BOARD_BATCH = 50    # tickers per KBS price_board call
FUND_BATCH_SLEEP  = 1.2   # seconds between Finance.ratio() calls (rate limit)

# ── Data paths ────────────────────────────────────────────────────────────────
DATA_DIR         = "data"
DAILY_DIR        = "data/daily"
FUND_FILE        = "data/fundamentals.parquet"   # EPS / BVPS cache (weekly refresh)
TICKER_HIST_FILE = "data/ticker_history.parquet" # daily PE/PB per ticker
SECTOR_HIST_FILE = "data/sector_history.parquet" # daily PE/PB per sector group

DOCS_DIR         = "docs"
DASHBOARD_FILE   = "docs/index.html"
JSON_FILE        = "docs/data_latest.json"

# ── Fundamentals cache max-age before the weekly job must refresh ─────────────
FUND_STALE_DAYS = 8   # if fundamentals file is older than this, warn in daily job

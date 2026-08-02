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
PRICE_BOARD_BATCH  = 50    # tickers per KBS price_board call

# Rate-limit settings for Finance.ratio() (weekly fundamentals fetch)
# Guest tier  : 20 req/min → 1 req per 3 s
# Sponsor tier: 60 req/min → 1 req per 1 s
# Finance.ratio() makes ~2 HTTP requests internally, so effective rate
# is doubled. Values below are safe for Guest tier (worst case).
FUND_BATCH_SLEEP   = 4.0   # base sleep (s) between each Finance.ratio() call
FUND_BATCH_EVERY   = 15    # pause every N tickers to let the rate window reset
FUND_BATCH_PAUSE   = 45    # longer pause duration (s) every FUND_BATCH_EVERY tickers
FUND_MAX_RETRIES   = 3     # max retries per ticker on rate-limit / transient errors
FUND_RETRY_WAIT    = 90    # base wait (s) on 429; multiplied by attempt number

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

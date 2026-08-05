"""Prints weekly fundamentals job summary to stdout → redirected to $GITHUB_STEP_SUMMARY."""
import sys
from pathlib import Path
import pandas as pd

parquet = Path("data/fundamentals.parquet")
if not parquet.exists():
    print("⚠️ data/fundamentals.parquet not found after fetch.")
    sys.exit(1)

df         = pd.read_parquet(parquet)
total      = len(df)
valid_eps  = df["eps_ttm"].notna().sum()
valid_bvps = df["bvps"].notna().sum()

print("| Metric | Value |")
print("|--------|-------|")
print(f"| Total tickers | {total} |")
print(f"| Valid TTM EPS | {valid_eps} ({valid_eps/total*100:.0f}%) |")
print(f"| Valid BVPS    | {valid_bvps} ({valid_bvps/total*100:.0f}%) |")
print()

if "eps_method" in df.columns:
    print("### EPS method breakdown")
    print()
    print("| Method | Count |")
    print("|--------|-------|")
    for method, cnt in df["eps_method"].value_counts().items():
        print(f"| {method} | {cnt} |")
    print()

missing_eps  = sorted(df[df["eps_ttm"].isna()]["ticker"].tolist())
missing_bvps = sorted(df[df["bvps"].isna()]["ticker"].tolist())

if missing_eps:
    print(f"### ❌ Missing TTM EPS — {len(missing_eps)} tickers")
    print()
    for i in range(0, len(missing_eps), 10):
        print("  " + ", ".join(missing_eps[i:i+10]))
    print()
else:
    print("### ✅ All tickers have valid TTM EPS")
    print()

if missing_bvps:
    print(f"### ❌ Missing BVPS — {len(missing_bvps)} tickers")
    print()
    for i in range(0, len(missing_bvps), 10):
        print("  " + ", ".join(missing_bvps[i:i+10]))
else:
    print("### ✅ All tickers have valid BVPS")

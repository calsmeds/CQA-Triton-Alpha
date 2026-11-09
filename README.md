# Daily compliance check: how to run

1. In StockTrak, export Open Positions as CSV (the `OpenPosition_<date>.csv` file).
2. From the StockTrak summary page, note **Current Portfolio Value** and **Cash Balance**.
3. Run (Python 3, no extra packages needed):

```
python3 check_compliance.py OpenPosition_11_9_2026.csv --pv 957000 --cash 12000
```

Optional flags:
- `--trades-used 180` checks the 1,000-trade budget.
- `--target-pct 4` is the weight oversized positions get trimmed to (default 4%).
- `--out trades.csv` saves the suggested trades to a file.
- Without `--pv`, value is estimated as $1M + total P&L. Without `--cash`, cash is skipped.

## What it checks
| Rule | Violation (contest) | Near-miss warning (our buffer) |
|---|---|---|
| Names per side | under 40 (38–39 minor, 37 or fewer major) | 40–42 names |
| Position weight | rounds to 6%+ (5.5%+ minor, 9%+ major) | above 4.5%; "watch" above 4% |
| Long/short ratio | outside 0.90–1.10 (0.85–1.18 minor) | outside 0.93–1.07 |
| Cash | rounds to 6%+ | above 4% |
| ETFs/ETNs | any (major), guessed from the name | – |

It also notes prices under $5 (you can't open new positions there) and non-USD listings.

## Reading the output
- **VIOLATIONS** would cost points if still there at Friday close.
- **NEAR-MISSES** are fine today but one bad day away.
- **SUGGESTED TRADES** give share counts to trim oversized positions to the target weight, plus the ratio afterwards.
- Exit code: 2 = violation, 1 = near-miss, 0 = clean.

Habit: run it daily, do the full fix on Wednesday, trade by Thursday, never Friday afternoon.
Check how StockTrak counts short sale proceeds in "cash" before trusting the cash check.

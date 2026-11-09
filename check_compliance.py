#!/usr/bin/env python3
"""
CQA Challenge daily compliance checker.

Reads a StockTrak "OpenPosition" CSV export and checks it against the
contest limits (2025-26 rulebook). Prints violations, near-misses and
suggested share quantities to trade.

Usage:
    python3 check_compliance.py OpenPosition_11_9_2026.csv --pv 957000 --cash 12000

    --pv     Current Portfolio Value from StockTrak. If left out, it is
             estimated as 1,000,000 + total P&L in the file.
    --cash   Cash balance from StockTrak. If left out, the cash check is skipped.

Run with --help for the other options.
"""
import argparse
import csv
import math
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

# ---- Contest limits (rulebook) -------------------------------------------
MIN_NAMES = 40            # >=40 OK, 38-39 minor, <=37 major
MAX_WEIGHT_PCT = 5        # rounded to whole %: <=5 OK, 6-8 minor, >=9 major
RATIO_OK = (0.90, 1.10)   # long MV / short MV, 2 decimals
RATIO_MINOR = (0.85, 1.18)
MAX_CASH_PCT = 5          # rounded to whole %: <=5 OK, 6-8 minor, >=9 major
TRADE_CAP = 1000

# ---- Our own buffers (early warnings, not contest rules) -----------------
NAMES_BUFFER = 3          # warn when a side has fewer than 40 + 3 names
WEIGHT_WARN_PCT = 4.5     # warn above this (unrounded)
WEIGHT_WATCH_PCT = 4.0    # team rule: rebalance anything above 4%
RATIO_WARN = (0.93, 1.07) # warn outside this band
CASH_WARN_PCT = 4.0
ETF_WORDS = ("ETF", "ETN", "EXCHANGE TRADED", "INDEX FUND", "ISHARES", "SPDR",
             "PROSHARES", "INVESCO QQQ", "VANGUARD", "DIREXION", "WISDOMTREE")

SEV_ORDER = {"MAJOR": 0, "MINOR": 1, "WARNING": 2, "WATCH": 3, "INFO": 4}


def num(s):
    s = (s or "").strip().replace(",", "").replace("$", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return float(s) if s not in ("", "-") else 0.0


def round_pct(x):
    """Round a percentage to a whole number, half up (5.5 -> 6), like the rulebook."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def round2(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def tier_from_rounded_pct(p, ok_max):
    if p <= ok_max:
        return None
    return "MINOR" if p <= 8 else "MAJOR"


def load_positions(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit(f"No rows found in {path}")
    needed = {"Symbol", "Quantity", "LastPrice", "MarketValue"}
    missing = needed - set(rows[0].keys())
    if missing:
        sys.exit(f"CSV is missing columns: {', '.join(sorted(missing))}")
    pos = []
    for r in rows:
        sym = (r.get("Symbol") or "").strip()
        if not sym:
            continue
        qty = num(r["Quantity"])
        price = num(r["LastPrice"])
        mv = abs(num(r["MarketValue"])) or abs(qty * price)
        pos.append({
            "symbol": sym,
            "desc": (r.get("Description") or "").strip(),
            "qty": qty,
            "side": "LONG" if qty > 0 else "SHORT",
            "price": price,
            "mv": mv,
            "pnl": num(r.get("ProfitLoss", "0")),
            "currency": (r.get("Currency") or "USD").strip(),
        })
    return pos


def main():
    ap = argparse.ArgumentParser(description="CQA daily compliance check on a StockTrak OpenPosition CSV.")
    ap.add_argument("csv", help="StockTrak OpenPosition export")
    ap.add_argument("--pv", type=float, help="Current Portfolio Value from StockTrak")
    ap.add_argument("--cash", type=float, help="Cash balance from StockTrak")
    ap.add_argument("--start-value", type=float, default=1_000_000,
                    help="Starting portfolio value, used to estimate PV if --pv is missing (default 1,000,000)")
    ap.add_argument("--target-pct", type=float, default=4.0,
                    help="Weight to trim oversized positions down to (default 4.0)")
    ap.add_argument("--trades-used", type=int, help="Trades used so far (from StockTrak transaction history)")
    ap.add_argument("--out", help="Optional path to write suggested trades as CSV")
    a = ap.parse_args()

    pos = load_positions(a.csv)
    longs = [p for p in pos if p["side"] == "LONG"]
    shorts = [p for p in pos if p["side"] == "SHORT"]
    long_mv = sum(p["mv"] for p in longs)
    short_mv = sum(p["mv"] for p in shorts)
    total_pnl = sum(p["pnl"] for p in pos)

    pv_estimated = a.pv is None
    pv = a.pv if a.pv else a.start_value + total_pnl

    findings = []   # (severity, rule, message)
    trades = []     # dicts: symbol, side, action, shares, est_value, reason

    def add(sev, rule, msg):
        findings.append((sev, rule, msg))

    # 1. Names per side
    for side, lst in (("Long", longs), ("Short", shorts)):
        n = len(lst)
        if n >= MIN_NAMES + NAMES_BUFFER:
            continue
        if n >= MIN_NAMES:
            add("WARNING", "Names per side",
                f"{side} side has {n} names (minimum 40). Losing {n - MIN_NAMES + 1} name(s) "
                f"would be a violation. Add {MIN_NAMES + NAMES_BUFFER - n} to keep a buffer.")
        else:
            sev = "MINOR" if n >= 38 else "MAJOR"
            add(sev, "Names per side",
                f"{side} side has {n} names (minimum 40). Add at least {MIN_NAMES - n}, "
                f"ideally {MIN_NAMES + NAMES_BUFFER - n}.")

    # 2. Position weights
    target_value = pv * a.target_pct / 100
    for p in sorted(pos, key=lambda x: -x["mv"]):
        w = p["mv"] / pv * 100
        if w <= WEIGHT_WATCH_PCT:
            break
        rp = round_pct(w)
        sev = tier_from_rounded_pct(rp, MAX_WEIGHT_PCT)
        if sev is None:
            sev = "WARNING" if w > WEIGHT_WARN_PCT else "WATCH"
        excess = p["mv"] - target_value
        shares = math.ceil(excess / p["price"]) if p["price"] else 0
        action = "SELL" if p["side"] == "LONG" else "COVER"
        rounded_note = f" (rounds to {rp}%)" if sev in ("MINOR", "MAJOR") else ""
        add(sev, "Position size",
            f"{p['symbol']} {p['side'].lower()} ${p['mv']:,.0f} = {w:.2f}% of PV{rounded_note}. "
            f"{action} {shares:,} shares to get to ~{a.target_pct:g}%.")
        trades.append({"symbol": p["symbol"], "side": p["side"], "action": action,
                       "shares": shares, "est_value": round(shares * p["price"], 2),
                       "reason": f"weight {w:.2f}% -> {a.target_pct:g}%"})

    # 3. Long/short ratio
    ratio = round2(long_mv / short_mv) if short_mv else float("inf")
    mid_gap = long_mv - short_mv
    fix_msg = (f"To get back to 1.00, add ~${-mid_gap:,.0f} of longs or cover ~${-mid_gap:,.0f} of shorts."
               if mid_gap < 0 else
               f"To get back to 1.00, add ~${mid_gap:,.0f} of shorts or sell ~${mid_gap:,.0f} of longs.")
    if not (RATIO_OK[0] <= ratio <= RATIO_OK[1]):
        sev = "MINOR" if RATIO_MINOR[0] <= ratio <= RATIO_MINOR[1] else "MAJOR"
        add(sev, "Long/short ratio", f"Ratio is {ratio:.2f} (allowed 0.90-1.10). {fix_msg}")
    elif not (RATIO_WARN[0] <= ratio <= RATIO_WARN[1]):
        add("WARNING", "Long/short ratio", f"Ratio is {ratio:.2f}, close to the 0.90-1.10 edge. {fix_msg}")

    # 4. Cash
    if a.cash is not None:
        cash_pct = a.cash / pv * 100
        rc = round_pct(cash_pct)
        sev = tier_from_rounded_pct(rc, MAX_CASH_PCT)
        target_cash = pv * 0.03
        if sev:
            add(sev, "Cash", f"Cash ${a.cash:,.0f} = {cash_pct:.2f}% of PV (rounds to {rc}%, max 5%). "
                f"Invest ~${a.cash - target_cash:,.0f}, split evenly long and short to keep the ratio.")
        elif cash_pct > CASH_WARN_PCT:
            add("WARNING", "Cash", f"Cash ${a.cash:,.0f} = {cash_pct:.2f}% of PV, close to the 5% cap. "
                f"Invest ~${a.cash - target_cash:,.0f}, split evenly long and short.")
    else:
        add("INFO", "Cash", "Cash not checked. Pass --cash with the balance from the StockTrak summary page.")

    # 5. ETFs / ETNs (name-based guess; confirm by hand)
    for p in pos:
        d = p["desc"].upper()
        if any(w in d for w in ETF_WORDS):
            add("MAJOR", "ETF/ETN", f"{p['symbol']} ({p['desc']}) looks like an ETF/ETN. "
                f"{'Sell' if p['side'] == 'LONG' else 'Cover'} all {abs(p['qty']):,.0f} shares if it is one.")

    # 6. Other notes
    for p in pos:
        if p["currency"].upper() != "USD":
            add("WARNING", "Listing", f"{p['symbol']} is quoted in {p['currency']}. Only US-listed stocks/ADRs are allowed.")
        if 0 < p["price"] < 5:
            add("INFO", "Price under $5", f"{p['symbol']} is at ${p['price']:.2f}. You can hold it, "
                f"but you can't open new positions under $5.")
    if a.trades_used is not None:
        left = TRADE_CAP - a.trades_used
        sev = "WARNING" if left < 200 else "INFO"
        add(sev, "Trade budget", f"{a.trades_used} of {TRADE_CAP} trades used, {left} left (buy + sell = 2).")

    # ---- Report ----------------------------------------------------------
    today = date.today()
    days_to_fri = (4 - today.weekday()) % 7
    print("=" * 72)
    print(f"CQA compliance check  |  {a.csv}  |  run {today:%a %b %d, %Y}")
    print("=" * 72)
    pv_note = " (ESTIMATED: start value + P&L; pass --pv for the real number)" if pv_estimated else ""
    print(f"Portfolio value : ${pv:,.0f}{pv_note}")
    print(f"Longs           : {len(longs):>3} names   ${long_mv:,.0f}")
    print(f"Shorts          : {len(shorts):>3} names   ${short_mv:,.0f}")
    print(f"Long/short ratio: {ratio:.2f}")
    print(f"Total P&L       : ${total_pnl:,.0f} ({total_pnl / a.start_value * 100:+.2f}% of start)")
    if a.cash is not None:
        print(f"Cash            : ${a.cash:,.0f} ({a.cash / pv * 100:.2f}%)")
    print(f"Limits are checked at Friday close: {'today' if days_to_fri == 0 else f'{days_to_fri} day(s) away'}.")
    print()

    findings.sort(key=lambda f: SEV_ORDER[f[0]])
    real = [f for f in findings if f[0] in ("MAJOR", "MINOR")]
    near = [f for f in findings if f[0] in ("WARNING", "WATCH")]
    info = [f for f in findings if f[0] == "INFO"]

    if not real and not near:
        print("ALL CLEAR: no violations or near-misses.")
    if real:
        print(f"VIOLATIONS ({len(real)}): fix before Friday close")
        for sev, rule, msg in real:
            print(f"  [{sev}] {rule}: {msg}")
        print()
    if near:
        print(f"NEAR-MISSES ({len(near)})")
        for sev, rule, msg in near:
            print(f"  [{sev}] {rule}: {msg}")
        print()
    if info:
        for sev, rule, msg in info:
            print(f"  [INFO] {rule}: {msg}")
        print()

    if trades:
        print("SUGGESTED TRADES")
        for t in trades:
            print(f"  {t['action']:<6} {t['shares']:>7,} {t['symbol']:<6} ~${t['est_value']:>10,.0f}   {t['reason']}")
        freed = sum(t["est_value"] for t in trades if t["side"] == "SHORT") - \
                sum(t["est_value"] for t in trades if t["side"] == "LONG")
        new_ratio = (long_mv - sum(t["est_value"] for t in trades if t["side"] == "LONG")) / \
                    (short_mv - sum(t["est_value"] for t in trades if t["side"] == "SHORT"))
        print(f"  Ratio after these trades: {new_ratio:.2f}"
              + (f". Re-deploy ~${abs(freed):,.0f} into new {'shorts' if freed > 0 else 'longs'} "
                 f"to keep the ratio and the name count." if abs(freed) > 1000 else ""))
        print()

    if a.out:
        with open(a.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["symbol", "side", "action", "shares", "est_value", "reason"])
            w.writeheader()
            w.writerows(trades)
        print(f"Suggested trades written to {a.out}")

    # Exit code: 2 = violation, 1 = near-miss, 0 = clean (handy for automation)
    sys.exit(2 if real else 1 if near else 0)


if __name__ == "__main__":
    main()

"""Whole-market calculations done once on the price panels (dates x tickers):
liquidity, average daily range, moving averages and relative-strength ranks."""
from __future__ import annotations

import numpy as np
import pandas as pd


def metrics(bars: dict[str, pd.DataFrame], cfg: dict, exclude: set[str]) -> pd.DataFrame:
    """One row per stock with the numbers the filters and ratings need."""
    c, h, l, v = bars["Close"], bars["High"], bars["Low"], bars["Volume"]
    cols = [t for t in c.columns if t not in exclude]
    c, h, l, v = c[cols], h[cols], l[cols], v[cols]

    last = c.iloc[-1]
    adr = (h / l - 1).rolling(20).mean().iloc[-1]
    dvol = (c * v).rolling(20).mean().iloc[-1]
    m = pd.DataFrame({
        "close": last,
        "adr": adr,
        "dollar_vol": dvol,
        "sma10": c.rolling(10).mean().iloc[-1],
        "sma20": c.rolling(20).mean().iloc[-1],
        "sma50": c.rolling(50).mean().iloc[-1],
        "bars": c.notna().sum(),
        "chg": c.iloc[-1] / c.iloc[-2] - 1,
        "p1m": c.iloc[-1] / c.iloc[-22] - 1,
        "p3m": c.iloc[-1] / c.iloc[-64] - 1 if len(c) > 64 else np.nan,
        "p6m": c.iloc[-1] / c.iloc[-127] - 1 if len(c) > 127 else np.nan,
    })
    u = cfg["universe"]
    m["liquid"] = (
        (m["close"] >= u["min_price"]) & (m["dollar_vol"] >= u["min_dollar_volume"])
        & (m["bars"] >= 60) & c.iloc[-1].notna() & v.iloc[-1].notna()
    )
    # Relative strength: percentile of each stock's gain among liquid stocks, per window
    liq = m[m["liquid"]]
    for k in ("p1m", "p3m", "p6m"):
        m["rs_" + k[1:]] = (liq[k].rank(pct=True) * 100).reindex(m.index)
    m["rs"] = m[["rs_1m", "rs_3m", "rs_6m"]].max(axis=1)
    m["leader"] = (
        m["liquid"] & (m["adr"] * 100 >= u["min_adr_pct"]) & (m["rs"] >= u["min_rs_rank"])
        & (m["close"] > m["sma50"])
    )
    return m


def breadth(bars: dict[str, pd.DataFrame], m: pd.DataFrame) -> dict:
    """How the average liquid stock is doing, plus a direct check on whether
    recent breakouts are holding."""
    liq = m.index[m["liquid"]]
    c, h, l, v = (bars[f][liq] for f in ("Close", "High", "Low", "Volume"))
    last = c.iloc[-1]
    n = int(last.notna().sum())
    chg = c.iloc[-1] / c.iloc[-2] - 1
    volup = v.iloc[-1] > v.rolling(50).mean().iloc[-1]
    look = min(len(c), 252)
    hi, lo = h.iloc[-look:].max(), l.iloc[-look:].min()

    # Breakout follow-through: stocks that closed at a 50-day high on 1.5x volume
    # 3 to 10 sessions ago. What share is still above that breakout close?
    prior_high = h.rolling(50).max().shift(1)
    avg_vol = v.rolling(50).mean().shift(1)
    fired = (c > prior_high) & (v > 1.5 * avg_vol)
    wins = total = 0
    for d in range(3, 11):
        if d + 1 > len(c):
            break
        mask = fired.iloc[-1 - d]
        # count a stock once: only its first breakout day in the window
        mask = mask & ~fired.iloc[-d - 11:-1 - d].any()
        total += int(mask.sum())
        wins += int((last[mask] > c.iloc[-1 - d][mask]).sum())
    return {
        "stocks": n,
        "above20": float((last > c.rolling(20).mean().iloc[-1]).sum() / max(n, 1) * 100),
        "above50": float((last > c.rolling(50).mean().iloc[-1]).sum() / max(n, 1) * 100),
        "up4": int(((chg >= 0.04) & volup).sum()),
        "down4": int(((chg <= -0.04) & volup).sum()),
        "new_highs": int((h.iloc[-1] >= hi).sum()),
        "new_lows": int((l.iloc[-1] <= lo).sum()),
        "followthrough": float(wins / total * 100) if total >= 10 else None,
        "followthrough_n": total,
    }


def groups(bars: dict[str, pd.DataFrame], names: dict[str, str]) -> list[dict]:
    """Sector and theme ETFs ranked by a blend of 1-month and 3-month returns."""
    c = bars["Close"]
    rows = []
    for t, name in names.items():
        if t not in c or c[t].notna().sum() < 70:
            continue
        s = c[t].dropna()
        r = {"ticker": t, "name": name, "w1": s.iloc[-1] / s.iloc[-6] - 1,
             "m1": s.iloc[-1] / s.iloc[-22] - 1, "m3": s.iloc[-1] / s.iloc[-64] - 1}
        r["blend"] = 0.6 * r["m1"] + 0.4 * r["m3"]
        rows.append(r)
    rows.sort(key=lambda r: r["blend"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows

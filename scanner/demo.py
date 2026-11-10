"""Invented price data so the whole pipeline can run offline (sample report and tests).

Tickers contain digits on purpose: none of them is a real stock.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

N = 320  # sessions


def _ohlcv(close: np.ndarray, rng_pct: np.ndarray, vol: np.ndarray, rs: np.random.Generator,
           gaps: dict[int, float] | None = None):
    """Build open/high/low around a close path. rng_pct is each day's high-low range."""
    n = len(close)
    o = np.empty(n)
    o[0] = close[0]
    o[1:] = close[:-1] * (1 + rs.normal(0, 0.002, n - 1))
    for i, g in (gaps or {}).items():
        o[i] = close[i - 1] * (1 + g)
    body_hi, body_lo = np.maximum(o, close), np.minimum(o, close)
    spare = np.maximum(rng_pct * close - (body_hi - body_lo), 0.002 * close)
    up = rs.uniform(0.3, 0.7, n)
    h = body_hi + spare * up
    l = body_lo - spare * (1 - up)
    return o, h, l, close, vol


def _walk(rs, n, start, drift, sigma):
    return start * np.exp(np.cumsum(rs.normal(drift, sigma, n)))


def flag(rs, base_days=8, run=0.7, final_gap=0.012, tight=True, pullback=0.07,
         today_breakout=False, start=20.0):
    """Quiet stock -> sharp run -> short base that tightens on falling volume."""
    run_days = 22
    extra = 1 if today_breakout else 0
    quiet = N - run_days - base_days - extra
    a = _walk(rs, quiet, start, 0.0002, 0.012)
    b = a[-1] * np.exp(np.cumsum(np.full(run_days, np.log(1 + run) / run_days)
                                 + rs.normal(0, 0.006, run_days)))
    peak_close = b[-1]
    # base: dip over the first three days, then drift back up towards the highs
    target = peak_close * (1 - final_gap)
    dip = peak_close * (1 - pullback)
    c = np.concatenate([np.linspace(peak_close, dip, 4)[1:],
                        np.linspace(dip, target, base_days - 3 + 1)[1:]])
    c = c * (1 + rs.normal(0, 0.002 if tight else 0.02, base_days))
    c[-1] = target
    close = np.concatenate([a, b, c])
    r = np.concatenate([np.full(quiet, 0.035), np.full(run_days, 0.07),
                        np.linspace(0.06, 0.022, base_days) if tight
                        else np.full(base_days, 0.075)])
    v = np.concatenate([rs.uniform(0.8, 1.2, quiet) * 1.5e6, rs.uniform(2.0, 3.5, run_days) * 1.5e6,
                        np.linspace(2.2, 0.9, base_days) * 1.5e6 if tight
                        else rs.uniform(1.8, 2.6, base_days) * 1.5e6])
    if today_breakout:
        close = np.append(close, peak_close * 1.055)
        r = np.append(r, 0.075)
        v = np.append(v, 6.5e6)
    return _ohlcv(close, r, v, rs)


def episodic(rs, age=0, gap=0.19, start=30.0):
    """A dull stock that gaps up on enormous volume and holds."""
    g = N - 1 - age
    a = _walk(rs, g, start, 0.0001, 0.011)
    day0 = a[-1] * (1 + gap) * 1.04
    after = day0 * (1 + np.cumsum(rs.normal(0.004, 0.006, age))) if age else np.array([])
    close = np.concatenate([a, [day0], after])
    r = np.concatenate([np.full(g, 0.03), [0.08], np.full(age, 0.04)])
    v = np.concatenate([rs.uniform(0.8, 1.2, g) * 9e5, [9.5e6], np.full(age, 3.0e6)])
    return _ohlcv(close, r, v, rs, gaps={g: gap})


def make(seed: int = 7, end: pd.Timestamp | None = None):
    """Return (bars, meta). bars = {field: DataFrame}, meta = stock list with sectors."""
    rs = np.random.default_rng(seed)
    end = end or pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=N)
    data: dict[str, tuple] = {}

    # Indexes and group ETFs: a steady uptrend with a wobble
    for i, t in enumerate(list(C.INDEXES) + list(C.GROUPS)):
        if t in C.INDEXES:   # a clean uptrend, so the sample shows a green light
            c = _walk(rs, N, 100 + 7 * i, 0.0012, 0.004)
        else:
            c = _walk(rs, N, 100 + 7 * i, rs.normal(0.0004, 0.0008), 0.009)
        data[t] = _ohlcv(c, np.full(N, 0.014), rs.uniform(0.8, 1.2, N) * 4e7, rs)

    # Background market
    for i in range(360):
        c = _walk(rs, N, rs.uniform(8, 150), rs.normal(0.0003, 0.0012), rs.uniform(0.012, 0.035))
        rng_pct = np.full(N, rs.uniform(0.02, 0.07))
        data[f"N{i:03d}"] = _ohlcv(c, rng_pct, rs.uniform(0.6, 1.4, N) * rs.uniform(4e5, 6e6), rs)

    # Constructed setups
    data["FLAG1"] = flag(rs, base_days=8, run=0.85, final_gap=0.010)
    data["FLAG2"] = flag(rs, base_days=13, run=0.60, final_gap=0.015, start=44)
    data["FLAG3"] = flag(rs, base_days=6, run=0.48, final_gap=0.02, start=12)
    data["BUILD1"] = flag(rs, base_days=7, run=0.7, final_gap=0.09, pullback=0.12, start=61)
    data["BUILD2"] = flag(rs, base_days=6, run=0.55, final_gap=0.03, tight=False, start=27)
    data["TRIG1"] = flag(rs, base_days=9, run=0.75, final_gap=0.012, today_breakout=True, start=35)
    data["EPIC1"] = episodic(rs, age=0, gap=0.21)
    data["EPIC2"] = episodic(rs, age=2, gap=0.13, start=55)

    bars = {}
    for j, f in enumerate(("Open", "High", "Low", "Close", "Volume")):
        bars[f] = pd.DataFrame({t: d[j] for t, d in data.items()}, index=idx)

    sectors = [("Technology", "Semiconductors"), ("Technology", "Computer Software"),
               ("Health Care", "Biotechnology"), ("Industrials", "Aerospace"),
               ("Energy", "Uranium"), ("Consumer Discretionary", "Retail")]
    rows = []
    for t in data:
        if t in C.INDEXES or t in C.GROUPS:
            continue
        s = sectors[rs.integers(len(sectors))]
        rows.append({"symbol": t, "name": f"Invented company {t}", "sector": s[0], "industry": s[1]})
    return bars, pd.DataFrame(rows)

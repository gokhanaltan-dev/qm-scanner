"""Long-or-cash timing rules on XU100, XU030 and XBANK (index levels from Yahoo, daily bars).

A rule states at each close what it wants to hold from the next session's open. Idle cash earns
nothing. Index levels exclude dividends. No rule here places orders.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SYMS = {"XU100": "XU100.IS", "XU030": "XU030.IS", "XBANK": "XBANK.IS"}


def load(data_dir, as_of=None):
    ix = pd.read_parquet(f"{data_dir}/yahoo_indices.parquet")
    out = {}
    for k, s in SYMS.items():
        d = ix[ix.symbol == s].set_index("date").sort_index()[["Open", "High", "Low", "Close"]].dropna()
        out[k] = d
    common = out["XU100"].index.intersection(out["XU030"].index).intersection(out["XBANK"].index)
    if as_of:
        common = common[common <= pd.Timestamp(as_of)]
    return {k: v.loc[common] for k, v in out.items()}


def rsi(c, n=2):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def week_end(idx):
    wk = np.asarray(idx.isocalendar().week.values, dtype=int)
    return pd.Series(np.r_[wk[1:] != wk[:-1], False], index=idx)


# ---- single-index rules: return a boolean Series "want to be long after this close"

def r_sma(d, n):
    return d.Close > d.Close.rolling(n).mean()


def r_ema_cross(d, a=20, b=50):
    return d.Close.ewm(span=a, adjust=False).mean() > d.Close.ewm(span=b, adjust=False).mean()


def r_donchian(d, n_in, n_out):
    hi = d.Close.rolling(n_in).max().shift(1)
    lo = d.Close.rolling(n_out).min().shift(1)
    pos, out = False, []
    for c, h, l in zip(d.Close.values, hi.values, lo.values):
        if not pos and np.isfinite(h) and c > h:
            pos = True
        elif pos and np.isfinite(l) and c < l:
            pos = False
        out.append(pos)
    return pd.Series(out, index=d.index)


def r_tsmom_weekly(d, n=63):
    sig = (d.Close / d.Close.shift(n) - 1) > 0
    return sig.where(week_end(d.index)).ffill().fillna(False).astype(bool)


def r_pullback(d, trend=200, rsi_n=2, buy_below=10, exit_ma=5, max_hold=10):
    up = d.Close > d.Close.rolling(trend).mean()
    r = rsi(d.Close, rsi_n)
    ma = d.Close.rolling(exit_ma).mean()
    pos, held, out = False, 0, []
    for u, rv, c, m in zip(up.values, r.values, d.Close.values, ma.values):
        if pos:
            held += 1
            if c > m or held >= max_hold:
                pos = False
        elif u and np.isfinite(rv) and rv < buy_below:
            pos, held = True, 0
        out.append(pos)
    return pd.Series(out, index=d.index)


RULES = {
    "A close > SMA50": lambda d: r_sma(d, 50),
    "B close > SMA200": lambda d: r_sma(d, 200),
    "C EMA20 > EMA50": lambda d: r_ema_cross(d),
    "D 55-session high in, 20-session low out": lambda d: r_donchian(d, 55, 20),
    "E 20-session high in, 10-session low out": lambda d: r_donchian(d, 20, 10),
    "F 63-session return > 0, weekly": lambda d: r_tsmom_weekly(d),
    "G RSI(2) < 10 above SMA200, exit above SMA5": lambda d: r_pullback(d),
}


def rot_strongest(data, n=63, ma=50):
    """Weekly: hold whichever of the three indices has the best 63-session return, if it is above its SMA50."""
    idx = data["XU100"].index
    mom = pd.DataFrame({k: v.Close / v.Close.shift(n) - 1 for k, v in data.items()})
    above = pd.DataFrame({k: v.Close > v.Close.rolling(ma).mean() for k, v in data.items()})
    best = mom.fillna(-np.inf).idxmax(axis=1).where(mom.notna().all(axis=1))
    want = pd.Series([b if (isinstance(b, str) and above.at[d, b]) else None for d, b in best.items()], index=idx, dtype=object)
    we = week_end(idx)
    out, cur = [], None
    for d in idx:
        if we[d]:
            cur = want[d]
        out.append(cur)
    return pd.Series(out, index=idx, dtype=object)


def rot_bank_switch(data, n=63, ma=50):
    """Daily: XBANK when it is above its SMA50 and has out-run XU100 over 63 sessions; otherwise XU100
    when that is above its SMA50; otherwise cash."""
    b, x = data["XBANK"].Close, data["XU100"].Close
    bank = (b > b.rolling(ma).mean()) & ((b / b.shift(n)) > (x / x.shift(n)))
    xu = x > x.rolling(ma).mean()
    return pd.Series(np.where(bank, "XBANK", np.where(xu, "XU100", None)), index=x.index, dtype=object)


def simulate(data, want, bps=10, start=None):
    """want: Series of symbol-or-None decided at each close; the position changes at the next open."""
    idx = data["XU100"].index
    c = bps / 1e4
    eq, cur, entry_px, entry_d = 1.0, None, None, None
    curve, held, trades = [], [], []
    prev_want = None
    for i, d in enumerate(idx):
        target = prev_want if i > 0 else None
        if start is not None and d < pd.Timestamp(start):
            target = None
        if target != cur:
            if cur is not None:                       # sell at today's open
                o, pc = data[cur].Open.iat[i], data[cur].Close.iat[i - 1]
                eq *= (o / pc) * (1 - c)
                trades.append({"symbol": cur, "entry_date": entry_d, "exit_date": d,
                               "ret": o * (1 - c) / (entry_px * (1 + c)) - 1, "sessions": i - idx.get_loc(entry_d)})
            if target is not None:                    # buy at today's open
                o = data[target].Open.iat[i]
                eq *= (data[target].Close.iat[i] / o) * (1 - c)
                entry_px, entry_d = o, d
            cur = target
        elif cur is not None:
            eq *= data[cur].Close.iat[i] / data[cur].Close.iat[i - 1]
        curve.append(eq); held.append(cur)
        w = want.iat[i]
        prev_want = w if isinstance(w, str) else None
    E = pd.Series(curve, index=idx)
    if start is not None:
        E = E.loc[pd.Timestamp(start):]
        E = E / E.iloc[0]
    return E, pd.Series(held, index=idx, dtype=object), pd.DataFrame(trades)


def as_want(sig, sym):
    return pd.Series(np.where(sig.fillna(False).astype(bool), sym, None), index=sig.index, dtype=object)


def summary(E, held, trades, start, years):
    yrs = (E.index[-1] - E.index[0]).days / 365.25
    yr = E.groupby(E.index.year).last() / E.groupby(E.index.year).first() - 1
    T = trades[trades.entry_date >= pd.Timestamp(start)] if len(trades) else trades
    dd = float((E / E.cummax() - 1).min())
    cagr = float(E.iloc[-1] ** (1 / yrs) - 1)
    r = {"cagr_%": round(cagr * 100, 1), "max_dd_%": round(dd * 100, 1), "mar": round(cagr / abs(dd), 2) if dd < 0 else None,
         "invested_%": round(float(held.loc[E.index].notna().mean()) * 100), "trades": len(T),
         "worst_year_%": round(float(yr.min()) * 100), "neg_years": int((yr < 0).sum())}
    if len(T):
        r.update({"win_%": round(float((T.ret > 0).mean()) * 100), "avg_trade_%": round(float(T.ret.mean()) * 100, 1),
                  "avg_sessions": round(float(T.sessions.mean()))})
    return r


def buy_hold(d, start):
    s = d.Close.loc[pd.Timestamp(start):]
    return s / s.iloc[0]

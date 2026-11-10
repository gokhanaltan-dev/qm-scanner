"""Large-cap study: banks, a BIST30-like group and a BIST100-like group.

Index membership history is not in the data, so the groups are rebuilt each session from what was
known then:
  L100  the 100 largest by market value (60-session median) among the 200 most liquid shares
  L30   the 30 most traded shares inside L100
  BANKS listed banks that are among the 200 most liquid shares
These approximate BIST100 / BIST30; they are not the official constituents.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import core
from . import research as R

BANKS = ["AKBNK", "GARAN", "ISCTR", "YKBNK", "VAKBN", "HALKB", "SKBNK", "TSKB", "ALBRK", "ICBCT", "QNBTR",
         "KLNMA", "ENPRA"]


def groups(mk, xs, cfg):
    member = xs["member"]
    mc = mk.panels["MCAP"].ffill().rolling(60, min_periods=20).median()
    mrank = mc.where(member).rank(axis=1, ascending=False, method="first")
    l100 = mrank <= 100
    l30 = xs["med60"].where(l100).rank(axis=1, ascending=False, method="first") <= 30
    banks = member & member.columns.isin(BANKS)[None, :]
    rs = xs["rs"]
    out = {"L100": l100, "L30": l30, "BANKS": banks}
    out["rs_pct_L100"] = rs.where(l100).rank(axis=1, pct=True) * 100
    out["rs_rank_L100"] = rs.where(l100).rank(axis=1, ascending=False, method="first")
    return out


# ----------------------------------------------------------------------------- extra pattern

def pat_pullback(a, f, cfg):
    """Pullback in an uptrend: the 55-session high was made within the last 20 sessions, price has
    since come back at least 2 ATR from its 15-session closing high into the EMA20 zone (a low within
    0.25 ATR of EMA20 in the last 3 sessions) while still closing above SMA50. Pivot = today's high."""
    H, L, C = a["H"], a["L"], a["C"]
    atr, ema = f["atr"], f["ema20"]
    hi20, hi55 = core.roll_max(H, 20), core.roll_max(H, 55)
    c15 = core.roll_max(C, 15)
    zone = (L <= ema + 0.25 * atr).astype(float)
    touched = core.roll_max(zone, 3) > 0
    with np.errstate(invalid="ignore"):
        ok = (hi20 >= hi55) & (c15 - C >= 2.0 * atr) & touched & (C > f["sma50"]) & (C > ema - 1.0 * atr)
    return np.nan_to_num(ok, nan=0).astype(bool), H.copy(), core.roll_min(L, 5) - 1.0 * atr


R.PATTERNS["pullback"] = pat_pullback


def ledger(mk, xs, g, cfg, pattern, entry="stop_at_pivot", stop="v1", group="L100", date_from=None, rs_min=None,
           exit_line="ema20", regime=None):
    """Trade ledger for one pattern inside one group; relative strength is ranked inside L100."""
    cfg2 = cfg if rs_min is None else {**cfg, "relative_strength": {**cfg["relative_strength"], "min_percentile": rs_min}}
    rows = []
    counts = {"armed": 0, "triggered": 0}
    for tk in mk.panels["C"].columns:
        a = core.stock_arrays(mk, xs, tk)
        if a is None or len(a["C"]) < 300:
            continue
        a["member"] = g[group][tk].values[a["pos"]]
        if not a["member"].any():
            continue
        a["rs_pct"] = g["rs_pct_L100"][tk].values[a["pos"]]
        f = core.features(a["O"], a["H"], a["L"], a["C"], a["V"], cfg2)
        if exit_line != "ema20":              # trailing exit on a different average
            f = {**f, "ema20": f[exit_line]}
        if regime is not None:                # boolean Series on the market calendar
            a["regime_ok"] = regime.values[a["pos"]]
        d = pd.DatetimeIndex(a["dates"])
        i0 = int(d.searchsorted(pd.Timestamp(date_from))) if date_from is not None else 0
        r = R.run_setups(a, f, R.PATTERNS[pattern](a, f, cfg2), cfg2, R.ENTRIES[entry], stop, i0)
        counts["armed"] += r["armed"]; counts["triggered"] += r["triggered"]
        for t in r["trades"]:
            i = t["trigger_i"]
            if i < i0:
                continue
            rows.append({"ticker": tk, "sector": mk.meta["sector"].get(tk, "Unknown"), "signal": d[i],
                         "entry_date": d[t["entry_i"]], "exit_date": d[t["exit_i"]], "pivot": t["P"],
                         "entry": t["entry"], "stop": t["stop"], "risk": t["risk"], "exit": t["exit"],
                         "exit_reason": t["reason"].replace("EMA20", exit_line.upper()), "hold": t["hold"], "open": t["open"],
                         "false_breakout": t["false_breakout"], "r_gross": t["r_gross"],
                         "rs_pct": a["rs_pct"][i - 1], "med20_try": a["med20"][i - 1],
                         "in_L30": bool(g["L30"][tk].values[a["pos"][i - 1]]), "is_bank": tk in BANKS})
    T = pd.DataFrame(rows)
    if len(T):
        for bps in (10, 25, 50):
            k = bps / 1e4
            T[f"r_net_{bps}"] = (T["exit"] * (1 - k) - T["entry"] * (1 + k)) / T["risk"]
        T["ret_net_25"] = T["exit"] * (1 - 0.0025) / (T["entry"] * (1 + 0.0025)) - 1
        T["risk_pct"] = T["risk"] / T["entry"]
    return T, counts


# ----------------------------------------------------------------------------- portfolio sims

PF = {"start": 200_000.0, "risk": 0.01, "max_pos": 10, "max_pos_pct": 0.15, "max_sector": 3, "liq_pct": 0.001, "bps": 25}


def portfolio_from_ledger(T, mk, pf=PF, start_date=None):
    """Cash-and-positions simulation. Same-day candidates are taken in relative-strength order.
    Entries fill before that day's exits, so a slot freed today is reusable tomorrow."""
    c = pf["bps"] / 1e4
    dates = mk.dates if start_date is None else mk.dates[mk.dates >= pd.Timestamp(start_date)]
    Cf = mk.panels["C"].ffill()
    T = T[~T["open"]].sort_values(["entry_date", "rs_pct"], ascending=[True, False])
    by_entry = {d: grp for d, grp in T.groupby("entry_date")}
    cash = eq_prev = pf["start"]
    pos, eq, expo, taken, skipped = [], [], [], 0, {}
    for d in dates:
        if d in by_entry:
            for _, t in by_entry[d].iterrows():
                why = None
                if any(x["ticker"] == t["ticker"] for x in pos):
                    why = "already held"
                elif len(pos) >= pf["max_pos"]:
                    why = "portfolio full"
                elif sum(1 for x in pos if x["sector"] == t["sector"]) >= pf["max_sector"]:
                    why = "sector limit"
                else:
                    want = min(pf["risk"] * eq_prev / (t["risk"] / t["entry"]), pf["max_pos_pct"] * eq_prev)
                    liq = pf["liq_pct"] * (t["med20_try"] if np.isfinite(t["med20_try"]) else 0)
                    notional = min(want, liq, cash / (1 + c))
                    if notional < 0.25 * want:
                        why = "liquidity cap or no cash"
                if why:
                    skipped[why] = skipped.get(why, 0) + 1
                    continue
                cash -= notional * (1 + c)
                taken += 1
                pos.append({"ticker": t["ticker"], "sector": t["sector"], "sh": notional / t["entry"],
                            "exit_date": t["exit_date"], "exit": t["exit"]})
        keep = []
        for x in pos:
            if x["exit_date"] <= d:
                cash += x["sh"] * x["exit"] * (1 - c)
            else:
                keep.append(x)
        pos = keep
        mv = sum(x["sh"] * Cf.at[d, x["ticker"]] for x in pos)
        eq_prev = cash + mv
        eq.append(eq_prev); expo.append(mv / eq_prev if eq_prev > 0 else 0)
    return pd.Series(eq, index=dates), pd.Series(expo, index=dates), taken, skipped


def rotation(mk, xs, g, group="L100", n=10, exit_rank=25, regime=None, trend=True, bps=25, start_date=None,
             start=200_000.0):
    """Weekly relative-strength rotation. On the last session of each week rank the group by RS;
    sell holdings ranked worse than exit_rank (or out of the group, or below SMA50 if trend=True) and
    fill free slots with the best-ranked names not held, each at 1/n of equity. Orders fill at the
    next session's open; a locked or missing bar postpones the order to the following session.
    regime='sma200': hold nothing new while XU100 is below its 200-session average and sell everything."""
    c = bps / 1e4
    O, C, H, L = (mk.panels[k] for k in ("O", "C", "H", "L"))
    okp = mk.panels["OPEN_OK"] > 0.5
    Cf = C.ffill()
    sma50 = C.rolling(50, min_periods=50).mean()
    xu = mk.xu100
    xu200 = xu.rolling(200).mean()
    rs_rank = xs["rs"].where(g[group]).rank(axis=1, ascending=False, method="first")
    dates = mk.dates if start_date is None else mk.dates[mk.dates >= pd.Timestamp(start_date)]
    wk = np.asarray(dates.isocalendar().week.values, dtype=int)
    is_week_end = np.r_[wk[1:] != wk[:-1], False]      # the last bar is not treated as a week end
    cash = eq_prev = start
    hold = {}                       # ticker -> shares
    pend_buy, pend_sell = [], []
    eq, expo, n_trades = [], [], 0
    for k, d in enumerate(dates):
        # execute pending orders at today's open
        still = []
        for tk in pend_sell:
            if tk not in hold:
                continue
            if pd.notna(C.at[d, tk]) and H.at[d, tk] > L.at[d, tk]:
                px = O.at[d, tk] if okp.at[d, tk] and pd.notna(O.at[d, tk]) else L.at[d, tk]
                cash += hold.pop(tk) * px * (1 - c); n_trades += 1
            else:
                still.append(tk)
        pend_sell = still
        for tk in pend_buy:
            if tk in hold or len(hold) >= n:
                continue
            if pd.notna(C.at[d, tk]) and H.at[d, tk] > L.at[d, tk] and okp.at[d, tk] and pd.notna(O.at[d, tk]):
                notional = min(eq_prev / n, cash / (1 + c))
                if notional > 0.2 * eq_prev / n:
                    hold[tk] = notional / O.at[d, tk]; cash -= notional * (1 + c); n_trades += 1
        pend_buy = []
        mv = sum(sh * Cf.at[d, tk] for tk, sh in hold.items())
        eq_prev = cash + mv
        eq.append(eq_prev); expo.append(mv / eq_prev if eq_prev > 0 else 0)
        if is_week_end[k]:
            rk = rs_rank.loc[d]
            risk_on = True if regime is None else bool(xu.at[d] > xu200.at[d])
            for tk in list(hold):
                r = rk.get(tk, np.nan)
                bad_trend = trend and not (C.at[d, tk] > sma50.at[d, tk]) if pd.notna(C.at[d, tk]) else False
                if (not risk_on) or not np.isfinite(r) or r > exit_rank or bad_trend:
                    pend_sell.append(tk)
            if risk_on:
                free = n - (len(hold) - len(pend_sell))
                cand = rk.dropna().sort_values()
                for tk in cand.index:
                    if free <= 0 or cand[tk] > n:
                        break
                    if tk in hold and tk not in pend_sell:
                        continue
                    if trend and not (C.at[d, tk] > sma50.at[d, tk]):
                        continue
                    if tk not in hold:
                        pend_buy.append(tk); free -= 1
    return pd.Series(eq, index=dates), pd.Series(expo, index=dates), n_trades


def curve(E, a=None, b=None):
    s = E.loc[a:b] if a or b else E
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    tot = s.iloc[-1] / s.iloc[0]
    return {"cagr": round(float(tot ** (1 / yrs) - 1) * 100, 1), "total": round(float(tot - 1) * 100, 0),
            "max_dd": round(float((s / s.cummax() - 1).min()) * 100, 1)}


def equal_weight(mk, g, group="L100", start_date=None):
    """Equal-weight basket of the group, rebalanced daily, no costs: a generous benchmark."""
    r = mk.panels["C"].pct_change(fill_method=None)
    w = g[group].shift(1, fill_value=False)
    ret = r.where(w).mean(axis=1).fillna(0)
    if start_date:
        ret = ret.loc[pd.Timestamp(start_date):]
    return (1 + ret).cumprod()

"""BIST tight-base breakout: data assembly, indicators, setup state machine and trade model.

Everything here is deterministic and uses only information available at each decision time:
a setup is judged after the close of session i from bars up to i, a signal on session t is
confirmed with t's completed bar, and the earliest fill is the open of the next bar.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta

import numpy as np
import pandas as pd
import yaml
from numpy.lib.stride_tricks import sliding_window_view as swv

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config(path: str | None = None) -> dict:
    with open(path or os.path.join(HERE, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


# ----------------------------------------------------------------------------- data

@dataclass
class Market:
    dates: pd.DatetimeIndex            # market calendar (sessions with an XU100 close)
    xu100: pd.Series
    usdtry: pd.Series
    panels: dict                       # field -> DataFrame[dates x tickers], adjusted prices
    meta: pd.DataFrame                 # ticker -> name, sector
    quality: dict = field(default_factory=dict)
    latest_session: pd.Timestamp | None = None


def last_completed_session(dates, retrieved_ist: datetime, cfg: dict):
    """Drop a bar dated on the retrieval day unless the session had closed (plus a buffer)."""
    h, m = map(int, cfg["session_close_local"].split(":"))
    cutoff = (datetime.combine(retrieved_ist.date(), dtime(h, m)) + timedelta(minutes=20)).time()
    last = pd.Timestamp(max(dates))
    if last.date() == retrieved_ist.date() and retrieved_ist.time() < cutoff:
        return pd.Timestamp(sorted(set(dates))[-2]), True
    return last, False


def build_market(data_dir: str, cfg: dict, as_of: str | None = None) -> Market:
    isy = pd.read_parquet(os.path.join(data_dir, "isy_prices.parquet"))
    ya = pd.read_parquet(os.path.join(data_dir, "yahoo_prices.parquet"))
    with open(os.path.join(data_dir, "universe.json"), encoding="utf-8") as f:
        uni = pd.DataFrame(json.load(f))
    with open(os.path.join(data_dir, "provenance.json"), encoding="utf-8") as f:
        prov = json.load(f)
    q: dict = {"provenance": prov}

    isy["date"] = pd.to_datetime(isy["date"])
    dup = isy.duplicated(["ticker", "date"])
    q["duplicate_rows_dropped"] = int(dup.sum())          # vendor repeats some rows with identical prices
    isy = isy[~dup]
    retrieved = datetime.fromisoformat(prov["retrieved_istanbul_end"]).replace(tzinfo=None)
    last, dropped = last_completed_session(isy["date"].unique(), retrieved, cfg)
    q["incomplete_bar_dropped"] = bool(dropped)
    isy = isy[isy["date"] <= last]
    if as_of:
        isy = isy[isy["date"] <= pd.Timestamp(as_of)]

    # Market calendar and index level come from the same response rows as the prices.
    cal = isy.dropna(subset=["END_DEGER"]).groupby("date")["END_DEGER"].median()
    cal = cal[cal > 0]
    usd = isy.dropna(subset=["DD_DEGER"]).groupby("date")["DD_DEGER"].median().reindex(cal.index).ffill()

    n0 = len(isy)
    num = ["HG_KAPANIS", "HG_AOF", "HG_MIN", "HG_MAX", "HG_HACIM", "HGDG_KAPANIS", "HGDG_AOF", "HGDG_MIN", "HGDG_MAX"]
    for c in num:
        isy[c] = pd.to_numeric(isy[c], errors="coerce")
    ok = (isy[num] > 0).all(axis=1)
    q["rows_total"] = int(n0)
    q["rows_no_trade_or_bad"] = int((~ok).sum())
    isy = isy[ok].copy()
    isy = isy[isy["date"].isin(cal.index)]

    isy["C"] = isy["HGDG_KAPANIS"]
    isy["H"] = isy[["HGDG_MAX", "HGDG_KAPANIS"]].max(axis=1)
    isy["L"] = isy[["HGDG_MIN", "HGDG_KAPANIS"]].min(axis=1)
    isy["TO"] = isy["HG_HACIM"]                          # TRY turnover
    isy["V"] = isy["HG_HACIM"] / isy["HGDG_AOF"]         # shares, on the adjusted share basis
    isy["RAWC"] = isy["HG_KAPANIS"]
    isy["ADJ"] = isy["HGDG_KAPANIS"] / isy["HG_KAPANIS"]
    isy["NORANGE"] = isy["HG_MAX"] <= isy["HG_MIN"]
    isy["MCAP"] = pd.to_numeric(isy["PD"], errors="coerce") if "PD" in isy.columns else np.nan

    # Opening price: Yahoo's open/close ratio applied to the adjusted close. The ratio is immune to
    # split or dividend adjustments. The Yahoo bar is trusted only if its high/close and low/close
    # ratios agree with Is Yatirim's raw bar.
    ya = ya.rename(columns={"Open": "yo", "High": "yh", "Low": "yl", "Close": "yc", "Volume": "yv"})
    ya["ticker"] = ya["symbol"].str.replace(".IS", "", regex=False)
    ya["date"] = pd.to_datetime(ya["date"])
    m = isy.merge(ya[["ticker", "date", "yo", "yh", "yl", "yc"]], on=["ticker", "date"], how="left")
    tol = 0.012
    good = (m["yc"] > 0) & (m["yo"] > 0)
    hr = (m["yh"] / m["yc"]) / (m["HG_MAX"] / m["HG_KAPANIS"]) - 1
    lr = (m["yl"] / m["yc"]) / (m["HG_MIN"] / m["HG_KAPANIS"]) - 1
    good &= (hr.abs() <= tol) & (lr.abs() <= tol)
    good &= (m["yo"] <= m["yh"] * 1.0001) & (m["yo"] >= m["yl"] * 0.9999)
    m["O"] = np.where(good, m["C"] * m["yo"] / m["yc"], np.nan)
    m["O"] = np.minimum(np.maximum(m["O"], m["L"]), m["H"])
    m["OPEN_OK"] = good.astype(float)
    q["open_verified_share"] = float(good.mean())

    panels = {}
    for f in ["O", "H", "L", "C", "V", "TO", "RAWC", "ADJ", "OPEN_OK", "NORANGE", "MCAP"]:
        panels[f] = m.pivot(index="date", columns="ticker", values=f).reindex(cal.index)
    meta = uni[uni["type"] == "stock"].set_index("ticker")[["name", "sector", "industry"]]
    meta["sector"] = meta["sector"].fillna("Unknown")
    q["tickers_with_prices"] = int(panels["C"].shape[1])
    q["first_date"], q["last_date"] = str(cal.index[0].date()), str(cal.index[-1].date())
    return Market(dates=cal.index, xu100=cal, usdtry=usd, panels=panels, meta=meta, quality=q,
                  latest_session=cal.index[-1])


# ----------------------------------------------------------------------------- cross-section

def cross_section(mk: Market, cfg: dict) -> dict:
    """Universe membership, liquidity rank and relative-strength percentile, date by date."""
    C, TO = mk.panels["C"], mk.panels["TO"]
    u = cfg["universe"]
    has_bar = C.notna()
    sessions = has_bar.cumsum()
    med = TO.fillna(0.0).rolling(u["liquidity_window"], min_periods=u["liquidity_window"]).median()
    eligible = has_bar & (sessions >= u["min_sessions"])
    rank = med.where(eligible).rank(axis=1, ascending=False, method="first")
    member = rank <= u["size"]

    Cf = C.ffill()
    rs = None
    for k in cfg["relative_strength"]["lookbacks"]:
        rel = (Cf / Cf.shift(k)).div(mk.xu100 / mk.xu100.shift(k), axis=0) - 1
        rs = rel if rs is None else rs + rel
    rs = rs / len(cfg["relative_strength"]["lookbacks"])
    rs_m = rs.where(member)
    pct = rs_m.rank(axis=1, pct=True) * 100

    young = has_bar & (sessions < u["min_sessions"])
    med20 = TO.rolling(20, min_periods=10).median()
    return {"member": member, "liq_rank": rank, "rs": rs, "rs_pct": pct, "young": young, "med60": med,
            "med20": med20, "sessions": sessions}


# ----------------------------------------------------------------------------- indicators

def sma(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def ema(x, n):
    """EMA seeded with the simple mean of the first n values."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2.0 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def wilder_atr(h, l, c, n=14):
    """Wilder ATR: first value is the mean of the first n true ranges, then (prev*(n-1)+TR)/n."""
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    pc = np.roll(c, 1)
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = h[0] - l[0]
    out[n] = tr[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def roll_max(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = swv(x, n).max(axis=1)
    return out


def roll_min(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = swv(x, n).min(axis=1)
    return out


def roll_mean(x, n):
    return sma(x, n)


def features(O, H, L, C, V, cfg) -> dict:
    """Per-stock arrays on the stock's own completed sessions. Index i means 'known after close i'."""
    b = cfg["base"]
    n = len(C)
    W = b["window"]
    f = {"sma50": sma(C, b["sma_fast"]), "sma200": sma(C, b["sma_slow"]), "ema20": ema(C, cfg["execution"]["exit_ema"]),
         "atr": wilder_atr(H, L, C, b["atr_period"])}
    P = roll_max(H, W)
    base_low = roll_min(L, W)
    f["P"], f["base_low"] = P, base_low
    f["depth"] = (P - base_low) / P

    touch_ok = np.zeros(n, bool)
    span = np.zeros(n)
    if n >= W:
        Hw = swv(H, W)
        mask = Hw >= (1 - b["touch_tolerance"]) * P[W - 1:, None]
        first = mask.argmax(axis=1)
        last = W - 1 - mask[:, ::-1].argmax(axis=1)
        span[W - 1:] = last - first
        touch_ok[W - 1:] = (last - first) >= b["touch_min_separation"]
    f["touch_ok"], f["touch_span"] = touch_ok, span

    k_last, k_first = b["contraction_last"], b["contraction_first"]
    r_last = roll_max(H, k_last) - roll_min(L, k_last)
    r_first_end = roll_max(H, k_first) - roll_min(L, k_first)      # range of a 20-window ending at j
    r_first = np.full(n, np.nan)
    shift = W - k_first                                            # first 20 of the 40 ends 20 sessions back
    r_first[shift:] = r_first_end[:n - shift]
    with np.errstate(divide="ignore", invalid="ignore"):
        f["contr"] = np.where(r_first > 0, r_last / r_first, np.nan)

    v_last = roll_mean(V, b["volume_last"])
    v_prior_end = roll_mean(V, b["volume_prior"])
    v_prior = np.full(n, np.nan)
    v_prior[b["volume_last"]:] = v_prior_end[:n - b["volume_last"]]
    with np.errstate(divide="ignore", invalid="ignore"):
        f["volratio"] = np.where(v_prior > 0, v_last / v_prior, np.nan)

    s50 = f["sma50"]
    s50_prev = np.full(n, np.nan)
    k = b["sma_fast_rising_over"]
    s50_prev[k:] = s50[:n - k]
    f["sma50_prev"] = s50_prev
    f["trend_ok"] = (C > s50) & (s50 > f["sma200"]) & (s50 > s50_prev)
    f["struct_ok"] = ((f["depth"] <= b["max_depth"]) & touch_ok & (f["contr"] <= b["max_contraction_ratio"])
                      & (f["volratio"] <= b["max_volume_ratio"]))
    f["volavg"] = roll_mean(V, cfg["confirmation"]["volume_window"])
    f["low_n"] = roll_min(L, cfg["execution"]["stop_lookback"])
    f["hi252"] = roll_max(H, 252)
    f["hh55"] = roll_max(H, cfg["baseline"]["breakout_lookback"])
    r126 = np.full(n, np.nan)
    r126[126:] = C[126:] / C[:-126] - 1
    f["ret126"] = r126
    return f


# ----------------------------------------------------------------------------- score

def _lin(x, lo, hi, pts):
    if x is None or not np.isfinite(x):
        return 0.0
    return float(np.clip((x - lo) / (hi - lo), 0, 1) * pts)


def count_touches(Hwin, P, tol, sep):
    idx = np.flatnonzero(Hwin >= (1 - tol) * P)
    cnt, lastp = 0, -10 ** 9
    for j in idx:
        if j - lastp >= sep:
            cnt += 1
            lastp = j
    return cnt


def score_setup(m: dict, cfg: dict) -> dict:
    """Deterministic 0-100 heuristic. m holds the measurements named in config.yaml -> score."""
    s = cfg["score"]
    t = s["trend"]
    trend = (_lin(m["sma50_slope"], t["sma50_slope_20d"]["lo"], t["sma50_slope_20d"]["hi"], t["sma50_slope_20d"]["points"])
             + _lin(m["ret126"], t["return_126d"]["lo"], t["return_126d"]["hi"], t["return_126d"]["points"])
             + _lin(m["close_vs_hi252"], t["close_vs_252d_high"]["lo"], t["close_vs_252d_high"]["hi"], t["close_vs_252d_high"]["points"]))
    bs = s["base"]
    tp = bs["touches"]
    nt = int(m["touches"])
    touch_pts = tp[4] if nt >= 4 else tp.get(nt, 0)
    base = (_lin(m["depth"], bs["depth"]["lo"], bs["depth"]["hi"], bs["depth"]["points"])
            + _lin(m["contr"], bs["contraction_ratio"]["lo"], bs["contraction_ratio"]["hi"], bs["contraction_ratio"]["points"])
            + touch_pts)
    r = s["relative_strength"]["percentile"]
    p = m["rs_pct"]
    rs = 0.0 if not np.isfinite(p) else (p / r["lo"] * r["points_lo"] if p < r["lo"] else
                                         r["points_lo"] + _lin(p, r["lo"], r["hi"], r["points_hi"] - r["points_lo"]))
    v = s["volume"]
    vol = (_lin(m["volratio"], v["dryup_ratio"]["lo"], v["dryup_ratio"]["hi"], v["dryup_ratio"]["points"])
           + _lin(m["updown"], v["up_down_volume_ratio"]["lo"], v["up_down_volume_ratio"]["hi"], v["up_down_volume_ratio"]["points"]))
    pr = s["practicality"]
    lr = m["liq_rank"]
    liq = 0
    for bound in sorted(pr["liquidity_rank"]):
        if np.isfinite(lr) and lr <= bound:
            liq = pr["liquidity_rank"][bound]
            break
    prac = (_lin(m["atr_to_pivot"], pr["atr_to_pivot"]["lo"], pr["atr_to_pivot"]["hi"], pr["atr_to_pivot"]["points"])
            + _lin(m["stop_dist"], pr["stop_distance"]["lo"], pr["stop_distance"]["hi"], pr["stop_distance"]["points"]) + liq)
    parts = {"trend": round(trend, 1), "base": round(base, 1), "rs": round(rs, 1), "volume": round(vol, 1),
             "practicality": round(prac, 1)}
    parts["total"] = round(sum(parts.values()), 1)
    return parts


# ----------------------------------------------------------------------------- engine

def simulate_trade(a, f, t, P, cfg):
    """Model the trade that follows a confirmed signal on the stock's session index t.
    Returns ('trade'|'skip'|'pending', dict)."""
    ex = cfg["execution"]
    O, H, L, C, ok = a["O"], a["H"], a["L"], a["C"], a["open_ok"]
    n = len(C)
    atr_sig = f["atr"][t - 1]
    max_entry = min(P * (1 + ex["max_entry_pct"]), P + ex["max_entry_atr"] * atr_sig)
    stop = f["low_n"][t - 1] - ex["stop_buffer_atr"] * atr_sig
    info = {"signal_i": t, "P": P, "atr_sig": atr_sig, "max_entry": max_entry, "stop": stop}
    e = t + 1
    if e >= n:
        return "pending", info
    info["entry_i"] = e
    if not ok[e] or not np.isfinite(O[e]):
        return "skip", {**info, "reason": "open not verified"}
    if H[e] <= L[e]:
        return "skip", {**info, "reason": "no range on entry day (locked limit or single print)"}
    if O[e] <= P:
        return "skip", {**info, "reason": "open at or below pivot"}
    if O[e] > max_entry:
        return "skip", {**info, "reason": "open above maximum entry"}
    entry = O[e]
    risk = entry - stop
    if risk <= 0:
        return "skip", {**info, "reason": "stop not below entry"}
    pending, reason, hold = False, None, 0
    exit_i, exit_px, uncertain = None, None, False
    for s in range(e, n):
        tradable = H[s] > L[s]
        if s > e and pending:
            if tradable:
                exit_i, exit_px = s, (O[s] if ok[s] and np.isfinite(O[s]) else L[s])
                uncertain = not ok[s]
                break
            hold += 1
            continue
        if s > e and tradable and ok[s] and np.isfinite(O[s]) and O[s] <= stop:
            exit_i, exit_px, reason = s, O[s], "stop (gap through, filled at open)"
            break
        if tradable and L[s] <= stop:
            if s > e and not ok[s]:
                exit_i, exit_px, reason, uncertain = s, L[s], "stop (open unverified, filled at low)", True
            else:
                exit_i, exit_px, reason = s, stop, "stop"
            break
        hold += 1
        if not tradable and C[s] <= stop:
            pending, reason = True, "stop (locked, next tradable open)"
        elif C[s] < f["ema20"][s]:
            pending, reason = True, "close below EMA20"
        elif hold >= ex["max_holding_sessions"]:
            pending, reason = True, "60 sessions"
    open_trade = exit_i is None
    if open_trade:
        exit_i, exit_px, reason = n - 1, C[n - 1], "open (marked at last close)"
    fb_end = min(n - 1, t + ex["false_breakout_sessions"])
    false_bo = bool((C[t + 1:fb_end + 1] < P).any()) if fb_end > t else False
    return "trade", {**info, "entry": entry, "risk": risk, "exit_i": exit_i, "exit": exit_px, "reason": reason,
                     "hold": exit_i - e + 1, "open": open_trade, "uncertain_fill": uncertain,
                     "false_breakout": false_bo, "r_gross": (exit_px - entry) / risk}


def run_tight_base(a, f, cfg, ticker="", collect_daily=False):
    """State machine for one stock. a: arrays (dates,O,H,L,C,V,open_ok,member,rs_pct,liq_rank)."""
    b, cf = cfg["base"], cfg["confirmation"]
    C, H, L, V = a["C"], a["H"], a["L"], a["V"]
    n = len(C)
    atr, P_arr = f["atr"], f["P"]
    rs_ok = np.nan_to_num(a["rs_pct"], nan=-1) >= cfg["relative_strength"]["min_percentile"]
    cond = f["trend_ok"] & a["member"] & rs_ok
    dist = P_arr - C
    with np.errstate(invalid="ignore"):
        near = (dist / P_arr) <= b["max_pct_below_pivot"]
        armed_c = f["struct_ok"] & cond & near & (dist <= b["armed_max_atr"] * atr)
        forming_c = f["struct_ok"] & cond & near & (dist > b["armed_max_atr"] * atr) & (dist <= b["forming_max_atr"] * atr)
    setups, trades, skips = [], [], []
    setup, busy_until, pending_signal = None, -1, None
    start = max(b["sma_slow"] + b["sma_fast_rising_over"], 252)
    daily = {}
    for i in range(start, n):
        if setup is not None:
            s = setup
            if L[i] < s["base_low"]:
                s.update(state="INVALIDATED", end_i=i, why="frozen base low broken")
                setups.append(s); setup = None
                continue
            rng = H[i] - L[i]
            conf = (C[i] >= s["P"] + cf["close_buffer_atr"] * atr[i - 1] and V[i] > 0
                    and V[i] >= cf["volume_multiple"] * f["volavg"][i - 1]
                    and rng > 0 and (C[i] - L[i]) / rng >= cf["min_close_location"])
            if conf:
                s.update(state="CONFIRMED", end_i=i, why="confirmed")
                kind, tr = simulate_trade(a, f, i, s["P"], cfg)
                tr.update(setup_id=s["id"], ticker=ticker, armed_i=s["armed_i"], vol_mult=V[i] / f["volavg"][i - 1])
                s["trade_kind"] = kind
                if kind == "trade":
                    trades.append(tr); busy_until = tr["exit_i"] if not tr["open"] else n
                elif kind == "skip":
                    skips.append(tr)
                else:
                    pending_signal = tr
                setups.append(s); setup = None
                continue
            if not cond[i]:
                s.update(state="INVALIDATED", end_i=i, why="trend or relative strength failed")
                setups.append(s); setup = None
                continue
            if i - s["armed_i"] >= b["expiry_sessions"]:
                s.update(state="EXPIRED", end_i=i, why="10 sessions without confirmation")
                setups.append(s); setup = None
                continue
            if H[i] > s["P"]:
                s["crossed_i"] = i
            continue
        if i <= busy_until:
            continue
        if armed_c[i]:
            setup = {"id": f"{ticker}-{pd.Timestamp(a['dates'][i]).strftime('%Y%m%d')}", "ticker": ticker,
                     "armed_i": i, "P": float(P_arr[i]), "base_low": float(f["base_low"][i]), "state": "ARMED",
                     "win_start_i": i - b["window"] + 1}
    out = {"setups": setups, "trades": trades, "skips": skips, "active": setup, "pending_signal": pending_signal,
           "forming_last": bool(forming_c[n - 1]) if n > start else False, "busy": busy_until >= n - 1 and busy_until >= 0,
           "armed_c": armed_c, "forming_c": forming_c, "cond": cond}
    return out


def run_baseline(a, f, cfg, ticker=""):
    """Simple 55-session-high breakout with the same next-open entry, stop, exits and one position per share."""
    C = a["C"]
    n = len(C)
    hh = f["hh55"]
    trades, skips = [], []
    busy_until = -1
    start = 252
    for i in range(start, n):
        if i <= busy_until:
            continue
        if not a["member"][i - 1] or not np.isfinite(hh[i - 1]) or not np.isfinite(f["atr"][i - 1]):
            continue
        if C[i] > hh[i - 1]:
            kind, tr = simulate_trade(a, f, i, float(hh[i - 1]), cfg)
            tr.update(ticker=ticker, setup_id=f"B55-{ticker}-{i}")
            if kind == "trade":
                trades.append(tr); busy_until = tr["exit_i"] if not tr["open"] else n
            elif kind == "skip":
                skips.append(tr)
    return {"trades": trades, "skips": skips}


def stock_arrays(mk: Market, xs: dict, ticker: str):
    c = mk.panels["C"][ticker]
    idx = np.flatnonzero(c.notna().values)
    if len(idx) == 0:
        return None
    g = lambda fld: mk.panels[fld][ticker].values[idx]
    a = {"dates": mk.dates.values[idx], "pos": idx, "O": g("O"), "H": g("H"), "L": g("L"), "C": g("C"),
         "V": g("V"), "TO": g("TO"), "open_ok": g("OPEN_OK") > 0.5,
         "member": xs["member"][ticker].values[idx], "rs_pct": xs["rs_pct"][ticker].values[idx],
         "liq_rank": xs["liq_rank"][ticker].values[idx], "med20": xs["med20"][ticker].values[idx],
         "rawc": g("RAWC"), "adj": g("ADJ")}
    return a


def setup_measurements(a, f, i, P, arm_i, cfg):
    """Measurements for the score at session i for a setup whose base window ended at arm_i."""
    b = cfg["base"]
    W = b["window"]
    lo = arm_i - W + 1
    Hw, Cw, Vw = a["H"][lo:arm_i + 1], a["C"][lo:arm_i + 1], a["V"][lo:arm_i + 1]
    prev = a["C"][lo - 1:arm_i]
    up, dn = Vw[Cw > prev].sum(), Vw[Cw < prev].sum()
    atr = f["atr"][i]
    conf = P + cfg["confirmation"]["close_buffer_atr"] * atr
    stop = f["low_n"][i] - cfg["execution"]["stop_buffer_atr"] * atr
    return {"sma50_slope": f["sma50"][i] / f["sma50_prev"][i] - 1, "ret126": f["ret126"][i],
            "close_vs_hi252": a["C"][i] / f["hi252"][i] if np.isfinite(f["hi252"][i]) else np.nan,
            "depth": f["depth"][arm_i], "contr": f["contr"][arm_i],
            "touches": count_touches(Hw, P, b["touch_tolerance"], b["touch_min_separation"]),
            "rs_pct": a["rs_pct"][i], "volratio": f["volratio"][arm_i], "updown": (up / dn) if dn > 0 else np.inf,
            "atr_to_pivot": (P - a["C"][i]) / atr, "stop_dist": (conf - stop) / conf, "liq_rank": a["liq_rank"][i]}


def tick_size(price, cfg):
    t = cfg["ticks"][0][1]
    for lo, step in cfg["ticks"]:
        if price >= lo:
            t = step
    return t


def round_tick(price, cfg, mode):
    t = tick_size(price, cfg)
    q = price / t
    q = np.ceil(q - 1e-9) if mode == "up" else np.floor(q + 1e-9)
    return round(float(q * t), 4)

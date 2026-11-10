"""V2 research harness: numeric chart-pattern detectors and entry-rule variants.

Protocol: design on development data only (market built with as_of=2021-12-31), then one look at
validation (as_of=2023-12-31, triggers from 2022). Data after 2023 is never loaded here.

Every pattern shares one gate (universe member, close > SMA50 > SMA200 with SMA50 rising,
relative-strength percentile >= 80) and one proximity rule (close within 1 ATR and 5% below the
pivot), so differences between rows come from the pattern or the entry, not from the filter.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv

from . import core
from .backtest import stats

# ----------------------------------------------------------------------------- helpers


def swing_flags(H, L, k=2):
    """Fractal swing points. sh[j] is knowable only after the close of j+k."""
    n = len(H)
    sh, sl = np.zeros(n, bool), np.zeros(n, bool)
    if n >= 2 * k + 1:
        Hw, Lw = swv(H, 2 * k + 1), swv(L, 2 * k + 1)
        sh[k:n - k] = (Hw[:, k] >= Hw.max(axis=1)) & (Hw[:, k] > Hw.min(axis=1))
        sl[k:n - k] = (Lw[:, k] <= Lw.min(axis=1)) & (Lw[:, k] < Lw.max(axis=1))
    return sh, sl


def _touches(H, sh, P, W, tol, k=2):
    """First and last confirmed swing high within tol of the pivot, per window end."""
    n = len(H)
    first, last = np.full(n, -1), np.full(n, -1)
    if n >= W:
        Hw, Sw = swv(H, W), swv(sh, W).copy()
        Sw[:, W - k:] = False                          # not yet confirmed at the window end
        m = Sw & (Hw >= (1 - tol) * P[W - 1:, None])
        any_ = m.any(axis=1)
        f = m.argmax(axis=1)
        l = W - 1 - m[:, ::-1].argmax(axis=1)
        base = np.arange(n - W + 1)
        first[W - 1:] = np.where(any_, base + f, -1)
        last[W - 1:] = np.where(any_, base + l, -1)
    return first, last


def gate(a, f, cfg):
    return f["trend_ok"] & a["member"] & (np.nan_to_num(a["rs_pct"], nan=-1) >= cfg["relative_strength"]["min_percentile"])


# ----------------------------------------------------------------------------- patterns
# Each returns (ok, pivot, invalidation_low): arrays indexed by "known after the close of i".

def pat_leader_high(a, f, cfg):
    """Reference population: a leader sitting just under its 55-session high. No base shape at all."""
    P = core.roll_max(a["H"], 55)
    return np.isfinite(P), P, core.roll_min(a["L"], 20)


def pat_flat_base(a, f, cfg, W=30, tol=0.015, sep=5, max_depth=0.20, tight=0.60):
    """Flat base: two separate confirmed swing highs within 1.5% of the 30-session high, at least 5
    sessions apart; depth <= 20%; last-10 range <= 60% of the whole base range."""
    H, L = a["H"], a["L"]
    P, lo = core.roll_max(H, W), core.roll_min(L, W)
    sh, _ = swing_flags(H, L)
    first, last = _touches(H, sh, P, W, tol)
    r10 = core.roll_max(H, 10) - core.roll_min(L, 10)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = ((P - lo) / P <= max_depth) & (first >= 0) & (last - first >= sep) & (r10 <= tight * (P - lo))
    return ok, P, lo


def pat_ascending_triangle(a, f, cfg, W=40, tol=0.015, sep=8, max_depth=0.25):
    """Ascending triangle: two swing highs within 1.5% of the 40-session high at least 8 sessions
    apart, and the trough after the last touch is higher than the trough between the touches by
    at least 0.5 ATR. Both troughs must be real pullbacks (>= 1 ATR below the pivot)."""
    H, L, atr = a["H"], a["L"], f["atr"]
    n = len(H)
    P, lo = core.roll_max(H, W), core.roll_min(L, W)
    sh, _ = swing_flags(H, L)
    first, last = _touches(H, sh, P, W, tol)
    with np.errstate(invalid="ignore", divide="ignore"):
        cand = ((P - lo) / P <= max_depth) & (first >= 0) & (last - first >= sep)
    ok = np.zeros(n, bool)
    low_ref = np.full(n, np.nan)
    for i in np.flatnonzero(cand):
        t1, t2 = first[i], last[i]
        if i - t2 < 3:
            continue
        la, lb = L[t1 + 1:t2].min(), L[t2 + 1:i + 1].min()
        if lb >= la + 0.5 * atr[i] and P[i] - la >= 1.0 * atr[i] and P[i] - lb >= 1.0 * atr[i]:
            ok[i], low_ref[i] = True, lb
    return ok, P, low_ref


def pat_vcp(a, f, cfg):
    """Volatility contraction over three consecutive blocks of 30, 20 and 10 sessions: each block's
    high-low range (as a share of its high) is at most 75% of the block before; first <= 35%, last
    <= 10%; lows rise from the middle block to the last; the last block sits within 3% of the
    pivot (highest high of the last 30 sessions); volume in the last block is below the 60-session mean."""
    H, L, V = a["H"], a["L"], a["V"]
    n = len(H)
    def blk(w, back):
        hi, lw = core.roll_max(H, w), core.roll_min(L, w)
        out_h, out_l = np.full(n, np.nan), np.full(n, np.nan)
        out_h[back:], out_l[back:] = hi[:n - back], lw[:n - back]
        return out_h, out_l
    hC, lC = blk(10, 0)
    hB, lB = blk(20, 10)
    hA, lA = blk(30, 30)
    with np.errstate(invalid="ignore", divide="ignore"):
        rA, rB, rC = (hA - lA) / hA, (hB - lB) / hB, (hC - lC) / hC
        P = np.maximum(hB, hC)
        ok = ((rA <= 0.35) & (rB <= 0.75 * rA) & (rC <= 0.75 * rB) & (rC <= 0.10) & (lC > lB)
              & (hC >= 0.97 * P) & (core.roll_mean(V, 10) < core.roll_mean(V, 60)))
    return np.nan_to_num(ok, nan=0).astype(bool), P, lC


def pat_bull_flag(a, f, cfg, look=20, min_flag=3, max_flag=15, pole_len=15, pole_gain=0.25, max_retrace=0.5):
    """Bull flag: the 20-session high (pole top) is 3 to 15 sessions old; price rose at least 25%
    into it within 15 sessions; the pullback since has given back at most half of the pole."""
    H, L = a["H"], a["L"]
    n = len(H)
    ok = np.zeros(n, bool)
    P, low_ref = np.full(n, np.nan), np.full(n, np.nan)
    if n < look + pole_len + 1:
        return ok, P, low_ref
    Hw = swv(H, look)
    age = Hw[:, ::-1].argmax(axis=1)                    # sessions since the most recent occurrence of the high
    idx = np.arange(look - 1, n)
    top = idx - age
    base = core.roll_min(L, pole_len + 1)
    for i, j, ag in zip(idx, top, age):
        if ag < min_flag or ag > max_flag or j < pole_len:
            continue
        b = base[j]
        if not np.isfinite(b) or b <= 0 or H[j] / b - 1 < pole_gain:
            continue
        fl = L[j + 1:i + 1].min()
        if (H[j] - fl) <= max_retrace * (H[j] - b):
            ok[i], P[i], low_ref[i] = True, H[j], fl
    return ok, P, low_ref


def pat_tight_v1(a, f, cfg):
    return f["struct_ok"], f["P"], f["base_low"]


PRIORITY = ["asc_triangle", "flat_base", "vcp", "bull_flag"]   # most specific label wins on one base


def pat_combined(a, f, cfg):
    """One setup per share: the first pattern in PRIORITY that holds on the day supplies pivot and low."""
    n = len(a["C"])
    ok, P, low = np.zeros(n, bool), np.full(n, np.nan), np.full(n, np.nan)
    atr, C = f["atr"], a["C"]
    b = cfg["base"]
    for name in PRIORITY:
        o, p, l = PATTERNS[name](a, f, cfg)
        with np.errstate(invalid="ignore", divide="ignore"):
            near = o & (p - C >= 0) & (p - C <= b["armed_max_atr"] * atr) & ((p - C) / p <= b["max_pct_below_pivot"])
        take = near & ~ok
        ok[take], P[take], low[take] = True, p[take], l[take]
    return ok, P, low


PATTERNS = {"leader_high": pat_leader_high, "flat_base": pat_flat_base, "asc_triangle": pat_ascending_triangle,
            "vcp": pat_vcp, "bull_flag": pat_bull_flag, "tight_v1": pat_tight_v1}
PATTERNS["combined"] = pat_combined

# ----------------------------------------------------------------------------- entries
ENTRIES = {
    # confirmed close, then next open
    "next_open_cap075": {"type": "next_open", "cap_atr": 0.75, "cap_pct": 0.03, "volume": True},
    "next_open_cap150": {"type": "next_open", "cap_atr": 1.50, "cap_pct": 0.06, "volume": True},
    "next_open_gap050": {"type": "next_open", "cap_atr": None, "cap_pct": None, "gap_atr": 0.5, "volume": True},
    "next_open_cap150_novol": {"type": "next_open", "cap_atr": 1.50, "cap_pct": 0.06, "volume": False},
    # resting buy-stop at the pivot
    "stop_at_pivot": {"type": "stop", "cap_atr": 0.75, "cap_pct": 0.03},
    "stop_at_pivot_failclose": {"type": "stop", "cap_atr": 0.75, "cap_pct": 0.03, "fail_close": True},
}
STOP_SLIP_ATR = 0.05      # adverse slippage assumed on an intraday stop-order fill, on top of costs


def simulate_exit(a, f, e, entry, stop, P, cfg, intraday=False, fail_level=None):
    ex = cfg["execution"]
    O, H, L, C, ok = a["O"], a["H"], a["L"], a["C"], a["open_ok"]
    n = len(C)
    pending, reason, hold = False, None, 0
    exit_i, exit_px = None, None
    for s in range(e, n):
        tradable = H[s] > L[s]
        if s > e and pending:
            if tradable:
                exit_i, exit_px = s, (O[s] if ok[s] and np.isfinite(O[s]) else L[s])
                break
            hold += 1
            continue
        if s > e and tradable and ok[s] and np.isfinite(O[s]) and O[s] <= stop:
            exit_i, exit_px, reason = s, O[s], "stop (gap through, filled at open)"
            break
        if tradable and L[s] <= stop:
            if s > e and not ok[s]:
                exit_i, exit_px, reason = s, L[s], "stop (open unverified, filled at low)"
            else:
                exit_i, exit_px = s, stop
                reason = "stop on entry day (order of prices unknown, assumed hit)" if (s == e and intraday) else "stop"
            break
        hold += 1
        if not tradable and C[s] <= stop:
            pending, reason = True, "stop (locked, next tradable open)"
        elif fail_level is not None and s == e and C[s] < fail_level:
            pending, reason = True, "closed back below pivot on entry day"
        elif C[s] < f["ema20"][s]:
            pending, reason = True, "close below EMA20"
        elif hold >= ex["max_holding_sessions"]:
            pending, reason = True, "60 sessions"
    is_open = exit_i is None
    if is_open:
        exit_i, exit_px, reason = n - 1, C[n - 1], "open (marked at last close)"
    fb_end = min(n - 1, e + ex["false_breakout_sessions"])
    return {"entry_i": e, "entry": entry, "stop": stop, "risk": entry - stop, "exit_i": exit_i, "exit": exit_px,
            "reason": reason, "hold": exit_i - e + 1, "open": is_open,
            "false_breakout": bool((C[e:fb_end + 1] < P).any()), "r_gross": (exit_px - entry) / (entry - stop)}


def stop_level(f, i, px, mode, cfg):
    ex = cfg["execution"]
    v1 = f["low_n"][i - 1] - ex["stop_buffer_atr"] * f["atr"][i - 1]
    if mode == "v1":
        return v1
    if mode == "atr15":
        return max(v1, px - 1.5 * f["atr"][i - 1])
    if mode == "atr10":
        return max(v1, px - 1.0 * f["atr"][i - 1])
    raise ValueError(mode)


def run_setups(a, f, pat, cfg, entry, stop_mode, count_from=0):
    """Freeze-at-arming state machine shared by every pattern, with a pluggable entry."""
    ok, P_arr, low_arr = pat
    b, cf = cfg["base"], cfg["confirmation"]
    O, H, L, C, V, okopen = a["O"], a["H"], a["L"], a["C"], a["V"], a["open_ok"]
    atr = f["atr"]
    n = len(C)
    g = gate(a, f, cfg)
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = P_arr - C
        arm = ok & g & (dist >= 0) & (dist <= b["armed_max_atr"] * atr) & (dist / P_arr <= b["max_pct_below_pivot"])
    res = {"armed": 0, "expired": 0, "invalidated": 0, "triggered": 0, "trades": [], "skips": {}}
    setup, busy = None, -1

    def skip(why):
        if i >= count_from:
            res["skips"][why] = res["skips"].get(why, 0) + 1

    def bump(key):
        if i >= count_from:
            res[key] += 1

    for i in range(252, n):
        if setup is not None:
            P, low = setup
            if np.isfinite(low) and L[i] < low:
                bump("invalidated"); setup = None; continue
            capx = np.inf
            if entry.get("cap_atr") is not None:
                capx = min(P * (1 + entry["cap_pct"]), P + entry["cap_atr"] * atr[i - 1])
            if entry["type"] == "stop":
                if H[i] > P:
                    bump("triggered"); setup = None
                    if not okopen[i] or not np.isfinite(O[i]):
                        skip("open not verified")
                    elif H[i] <= L[i]:
                        skip("no range on entry day")
                    elif O[i] > capx:
                        skip("open above maximum entry")
                    else:
                        gapped = O[i] > P
                        px = O[i] if gapped else min(H[i], P + STOP_SLIP_ATR * atr[i - 1])
                        st = stop_level(f, i, px, stop_mode, cfg)
                        if px - st > 0:
                            t = simulate_exit(a, f, i, px, st, P, cfg, intraday=not gapped,
                                              fail_level=P if entry.get("fail_close") else None)
                            t.update(trigger_i=i, P=P); res["trades"].append(t)
                            busy = t["exit_i"] if not t["open"] else n
                    continue
            else:
                rng = H[i] - L[i]
                conf = (C[i] >= P + cf["close_buffer_atr"] * atr[i - 1] and V[i] > 0 and rng > 0
                        and (C[i] - L[i]) / rng >= cf["min_close_location"]
                        and (not entry["volume"] or V[i] >= cf["volume_multiple"] * f["volavg"][i - 1]))
                if conf:
                    bump("triggered"); setup = None
                    e = i + 1
                    if e >= n:
                        skip("no next session yet"); continue
                    if not okopen[e] or not np.isfinite(O[e]):
                        skip("open not verified")
                    elif H[e] <= L[e]:
                        skip("no range on entry day")
                    elif O[e] <= P:
                        skip("open at or below pivot")
                    elif O[e] > capx:
                        skip("open above maximum entry")
                    elif entry.get("gap_atr") is not None and O[e] > C[i] + entry["gap_atr"] * atr[i - 1]:
                        skip("gap above signal close too large")
                    else:
                        st = stop_level(f, i, O[e], stop_mode, cfg)
                        if O[e] - st > 0:
                            t = simulate_exit(a, f, e, O[e], st, P, cfg)
                            t.update(trigger_i=i, P=P); res["trades"].append(t)
                            busy = t["exit_i"] if not t["open"] else n
                    continue
            if not g[i]:
                bump("invalidated"); setup = None; continue
            if i - setup_i >= b["expiry_sessions"]:
                bump("expired"); setup = None; continue
            continue
        if i <= busy:
            continue
        if arm[i]:
            setup, setup_i = (float(P_arr[i]), float(low_arr[i]) if np.isfinite(low_arr[i]) else np.nan), i
            bump("armed")
    return res


def run_grid(mk, xs, cfg, cells, date_from=None):
    """cells: list of (pattern, entry, stop_mode). Returns {cell: {'counts':..., 'trades': DataFrame}}."""
    out = {c: {"armed": 0, "expired": 0, "invalidated": 0, "triggered": 0, "skips": {}, "rows": []} for c in cells}
    pats_needed = sorted({c[0] for c in cells})
    for tk in mk.panels["C"].columns:
        a = core.stock_arrays(mk, xs, tk)
        if a is None or len(a["C"]) < 300:
            continue
        f = core.features(a["O"], a["H"], a["L"], a["C"], a["V"], cfg)
        if not gate(a, f, cfg).any():
            continue
        d = pd.DatetimeIndex(a["dates"])
        i0 = int(d.searchsorted(pd.Timestamp(date_from))) if date_from is not None else 0
        pat = {p: PATTERNS[p](a, f, cfg) for p in pats_needed}
        sector = mk.meta["sector"].get(tk, "Unknown")
        for c in cells:
            r = run_setups(a, f, pat[c[0]], cfg, ENTRIES[c[1]], c[2], i0)
            o = out[c]
            for k in ("armed", "expired", "invalidated", "triggered"):
                o[k] += r[k]
            for k, v in r["skips"].items():
                o["skips"][k] = o["skips"].get(k, 0) + v
            for t in r["trades"]:
                i = t["trigger_i"]
                o["rows"].append({"ticker": tk, "sector": sector, "signal": d[i], "entry_date": d[t["entry_i"]],
                                  "exit_date": d[t["exit_i"]], "pivot": t["P"], "entry": t["entry"], "stop": t["stop"],
                                  "risk": t["risk"], "exit": t["exit"], "exit_reason": t["reason"], "hold": t["hold"],
                                  "open": t["open"], "false_breakout": t["false_breakout"], "r_gross": t["r_gross"],
                                  "rs_pct": a["rs_pct"][i - 1], "liq_rank": a["liq_rank"][i - 1]})
    for c, o in out.items():
        T = pd.DataFrame(o.pop("rows"))
        if len(T):
            for bps in (10, 25, 50):
                k = bps / 1e4
                T[f"r_net_{bps}"] = (T["exit"] * (1 - k) - T["entry"] * (1 + k)) / T["risk"]
            T["ret_net_25"] = T["exit"] * (1 - 0.0025) / (T["entry"] * (1 + 0.0025)) - 1
            T["risk_pct"] = T["risk"] / T["entry"]
            if date_from is not None:
                T = T[T["signal"] >= pd.Timestamp(date_from)]
        o["trades"] = T
    return out


def summarise_cell(o, rng=None, years=None):
    T = o["trades"]
    T = T[~T["open"]] if len(T) else T
    s = stats(T, "r_net_25", rng) if len(T) else {"trades": 0}
    row = {"armed": o["armed"], "triggered": o["triggered"], "trades": s.get("trades", 0)}
    if s.get("trades", 0):
        row.update({"fill_rate": round(s["trades"] / max(o["triggered"], 1), 2), "win": s["win_rate"],
                    "mean_R": s["expectancy_R"], "ci90": s.get("expectancy_R_ci90"), "median_R": s["median_R"],
                    "avg_win": s["avg_win_R"], "avg_loss": s["avg_loss_R"], "pf": s["profit_factor"],
                    "mean_ret_pct": round(float(T["ret_net_25"].mean()) * 100, 2),
                    "risk_pct": round(float(T["risk_pct"].median()) * 100, 1),
                    "R_at_50bp": round(float(T["r_net_50"].mean()), 3),
                    "hold": s["avg_hold_sessions"], "false_bo": s["false_breakout_rate"],
                    "total_R": round(float(T["r_net_25"].sum()), 1)})
        if years:
            row["trades_per_year"] = round(s["trades"] / years, 1)
    return row

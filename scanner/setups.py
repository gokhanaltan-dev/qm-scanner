"""Setup detection and rating for one stock at a time.

Two setups, both long:
  * Breakout  - a big run-up, then an orderly, tightening base on drying volume,
                riding its rising 10/20-day averages, close to the top of its range.
  * Episodic pivot - a large gap up on a surge of volume, out of a liquid and
                previously quiet stock, that closes strong and stays near its high.

Every function takes plain numpy arrays (open, high, low, close, volume) and an
`end` index, so the same code answers "what did this look like yesterday?" which
is how a breakout that fired today is recognised.
"""
from __future__ import annotations

import math

import numpy as np


def _clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _sma(a: np.ndarray, n: int, end: int) -> float:
    if end + 1 < n:
        return float("nan")
    return float(np.nanmean(a[end - n + 1:end + 1]))


# ---------------------------------------------------------------------------
# Breakout base
# ---------------------------------------------------------------------------
def detect_base(o, h, l, c, v, end: int, cfg: dict) -> dict | None:
    """Describe the base ending at `end`, or return None if there is no valid one."""
    b = cfg["breakout"]
    if end < 70:
        return None

    # 1. The peak the base hangs from: highest high of the last max_base_days sessions
    start = end - b["max_base_days"]
    pk = start + int(np.nanargmax(h[start:end + 1]))
    base_len = end - pk
    if base_len < b["min_base_days"]:
        return None
    if h[pk] < np.nanmax(h[max(0, pk - 15):pk + 1]):  # not a real swing high
        return None

    # 2. The run-up into that peak
    # (starts at the most recent low within 8% of the 60-session low, so a long quiet
    # stretch before the move is not counted as part of it)
    lo0 = max(0, pk - 60)
    window = l[lo0:pk + 1]
    near_low = np.flatnonzero(window <= np.nanmin(window) * 1.08)
    lo_i = lo0 + int(near_low[-1])
    leg_low, peak = float(l[lo_i]), float(h[pk])
    leg_gain = peak / leg_low - 1
    leg_days = pk - lo_i
    rng = h / l - 1
    leg_adr = float(np.nanmean(rng[max(0, pk - 19):pk + 1]))
    if leg_days < 3 or leg_gain < max(b["min_leg_gain_pct"] / 100, 4 * leg_adr):
        return None

    # 3. Base shape
    base_low = float(np.nanmin(l[pk + 1:end + 1]))
    retrace = (peak - base_low) / (peak - leg_low)
    depth = 1 - base_low / peak
    if retrace > b["max_retrace"] or depth > 0.35:
        return None

    close = float(c[end])
    s10, s20, s50 = _sma(c, 10, end), _sma(c, 20, end), _sma(c, 50, end)
    if not close > s50:
        return None
    adr = float(np.nanmean(rng[end - 19:end + 1]))

    k = min(5, base_len)
    tight = float(np.nanmean(rng[end - k + 1:end + 1])) / leg_adr
    close_span = float((np.nanmax(c[end - k + 1:end + 1]) - np.nanmin(c[end - k + 1:end + 1])) / close) / adr

    half = pk + 1 + (base_len // 2)  # base bars are pk+1 .. end
    first_low = float(np.nanmin(l[pk + 1:half]))
    second_low = float(np.nanmin(l[half:end + 1]))
    higher_lows = second_low > first_low
    holding = second_low >= first_low * (1 - 0.25 * adr)

    vol_ratio = float(np.nanmean(v[end - k + 1:end + 1]) / np.nanmean(v[end - 49:end + 1]))

    s10_prev, s20_prev = _sma(c, 10, end - 3), _sma(c, 20, end - 5)
    ma_stack = close >= s10 and close >= s20 and s10 >= s20 and s20 > s20_prev
    ma_ok = close >= s20 and s20 > s20_prev
    ma_ref = s10 if base_len <= 15 else s20
    ma_dist = abs(close - ma_ref) / close / adr

    # 4. Trigger: the top of the recent tight range (the absolute peak for short bases)
    kp = min(base_len + 1, 10)
    pivot = float(np.nanmax(h[end - kp + 1:end + 1]))
    dist = (pivot / close - 1) / adr

    return {
        "peak": peak, "leg_low": leg_low, "leg_gain": leg_gain, "leg_days": int(leg_days),
        "base_days": int(base_len), "base_low": base_low, "depth": depth, "retrace": retrace,
        "tight": tight, "close_span": close_span, "higher_lows": bool(higher_lows),
        "holding": bool(holding), "vol_ratio": vol_ratio, "ma_stack": bool(ma_stack),
        "ma_ok": bool(ma_ok), "ma_dist": ma_dist, "sma10": s10, "sma20": s20, "sma50": s50,
        "adr": adr, "pivot": pivot, "dist": dist, "close": close,
        "s10_rising": bool(s10 > s10_prev),
    }


BREAKOUT_PARTS = [  # (key, label, max points)
    ("leader", "Leadership", 25),
    ("tight", "Tightness", 25),
    ("volume", "Volume dry-up", 10),
    ("ma", "Moving averages", 15),
    ("base", "Base shape", 10),
    ("trigger", "Near trigger", 10),
    ("tradeable", "Range & liquidity", 5),
]


def score_base(base: dict, rs: float, dollar_vol: float) -> dict:
    """0-100 rating. Each part is capped so no single strength can carry a weak setup."""
    p = {}
    p["leader"] = 15 * _clip01((rs - 85) / 14) + 10 * _clip01((base["leg_gain"] - 0.2) / 0.6)
    p["tight"] = (15 * _clip01((1.1 - base["tight"]) / 0.6)
                  + 5 * _clip01((1.5 - base["close_span"]) / 1.0)
                  + (5 if base["higher_lows"] else 2.5 if base["holding"] else 0))
    p["volume"] = 10 * _clip01((1.2 - base["vol_ratio"]) / 0.7)
    p["ma"] = ((10 if base["ma_stack"] else 5 if base["ma_ok"] else 0)
               + 5 * _clip01((2.0 - base["ma_dist"]) / 1.5))
    length_ok = 4 if 5 <= base["base_days"] <= 25 else 2
    p["base"] = 6 * _clip01((0.5 - base["retrace"]) / 0.25) + length_ok
    p["trigger"] = 10 * _clip01((1.5 - base["dist"]) / 1.25)
    p["tradeable"] = (3 * _clip01((base["adr"] - 0.03) / 0.03)
                      + 2 * _clip01(math.log10(max(dollar_vol, 1) / 1e7) / 0.7))
    total = sum(p.values())
    return {"score": round(total), "parts": {k: round(x, 1) for k, x in p.items()}}


def base_status(base: dict) -> str:
    """ready = tight and within one day's range of the trigger; otherwise still building."""
    if base["dist"] <= 1.0 and base["tight"] <= 0.85 and base["ma_ok"]:
        return "ready"
    return "building"


def detect_breakout_today(o, h, l, c, v, end: int, cfg: dict) -> dict | None:
    """Was there a valid base yesterday, and did today close above its trigger on volume?"""
    prev = detect_base(o, h, l, c, v, end - 1, cfg)
    if prev is None or prev["tight"] > 1.0:
        return None
    pivot = prev["pivot"]
    avg_vol = float(np.nanmean(v[end - 50:end]))
    vol_mult = float(v[end] / avg_vol) if avg_vol else float("nan")
    if not (c[end] > pivot and c[end] > o[end] and vol_mult >= 1.3):
        return None
    adr = prev["adr"]
    day_range = float(h[end] - l[end])
    return {
        **prev, "pivot": pivot, "close": float(c[end]), "low_of_day": float(l[end]),
        "high_of_day": float(h[end]), "vol_mult": vol_mult,
        "extension": (float(c[end]) / pivot - 1) / adr,
        "close_pos": float((c[end] - l[end]) / day_range) if day_range else 1.0,
        "day_gain": float(c[end] / c[end - 1] - 1),
    }


# ---------------------------------------------------------------------------
# Episodic pivot
# ---------------------------------------------------------------------------
# Weights follow the backtest: distance from the high was the one thing that separated
# gaps that worked from gaps that did not. Gap size and volume surge did not.
EP_PARTS = [
    ("near", "Near its high", 40),
    ("close", "Strong close", 20),
    ("fresh", "Fresh move", 15),
    ("gap", "Gap size", 10),
    ("volume", "Volume surge", 5),
    ("tradeable", "Range & liquidity", 10),
]


def detect_ep(o, h, l, c, v, end: int, cfg: dict) -> dict | None:
    """Most recent qualifying gap within the lookback that is still holding."""
    e = cfg["episodic_pivot"]
    if end < 70:
        return None
    for age in range(0, e["lookback_days"] + 1):
        g = end - age
        gap = float(o[g] / c[g - 1] - 1)
        if gap < e["min_gap_pct"] / 100:
            continue
        avg_vol = float(np.nanmean(v[g - 50:g]))
        if not avg_vol or v[g] / avg_vol < e["min_volume_multiple"]:
            continue
        day_range = float(h[g] - l[g])
        close_pos = float((c[g] - l[g]) / day_range) if day_range else 1.0
        # the gap day must not have been sold off, and nothing since may undercut its low
        if c[g] < o[g] * 0.97 or close_pos < 0.4:
            continue
        if age and float(np.nanmin(l[g + 1:end + 1])) < l[g]:
            continue
        # ... and price must still sit in the upper half of the range since the gap,
        # otherwise the move has faded and there is nothing to buy
        post_high = float(np.nanmax(h[g:end + 1]))
        if age and c[end] < l[g] + 0.5 * (post_high - l[g]):
            continue
        # ... and close to that high. In the backtest only gaps within half a daily range
        # of their high broke even or better; the further away, the worse they did.
        rng = h / l - 1
        adr_now = float(np.nanmean(rng[end - 19:end + 1]))
        if post_high / c[end] - 1 > e["max_adr_from_high"] * adr_now:
            continue
        # Thin, wild stocks are left out: their gaps fell about 15% against the market
        # over the next 10 sessions in the backtest.
        adr_before = float(np.nanmean(rng[g - 20:g]))
        pre_dollar_vol = float(np.nanmean(c[g - 50:g] * v[g - 50:g]))
        if (c[g - 1] < e["min_pre_price"] or adr_before * 100 >= e["max_prior_adr_pct"]
                or gap * 100 >= e["max_gap_pct"] or pre_dollar_vol < e["min_pre_dollar_volume"]):
            continue
        prior_3m = float(c[g - 1] / c[g - 64] - 1)
        high_6m = float(np.nanmax(h[max(0, g - 126):g]))
        pivot = float(np.nanmax(h[g:end + 1]))
        return {
            "age": age, "gap": gap, "vol_mult": float(v[g] / avg_vol), "close_pos": close_pos,
            "gap_day_gain": float(c[g] / c[g - 1] - 1), "gap_low": float(l[g]),
            "gap_high": float(h[g]), "prior_3m": prior_3m, "clears_6m_high": bool(c[g] > high_6m),
            "adr_before": adr_before, "adr": float(np.nanmean(rng[end - 19:end + 1])),
            "pivot": pivot, "close": float(c[end]), "green_day": bool(c[g] > o[g]),
            # the stock before the gap: used to screen out thin, wild names
            "pre_close": float(c[g - 1]),
            "pre_dollar_vol": pre_dollar_vol,
            "dist": (pivot / float(c[end]) - 1) / adr_now,
            "recent_low": float(np.nanmin(l[max(g, end - 1):end + 1])),
        }
    return None


def score_ep(ep: dict, dollar_vol: float) -> dict:
    p = {}
    p["near"] = 40 * _clip01((0.5 - ep["dist"]) / 0.5)
    p["gap"] = 10 * _clip01((ep["gap"] - 0.05) / 0.20)
    p["volume"] = 5 * _clip01((ep["vol_mult"] - 1.5) / 8.5)
    p["close"] = 15 * _clip01(ep["close_pos"]) + (5 if ep["green_day"] else 0)
    p["fresh"] = 10 * _clip01((0.6 - ep["prior_3m"]) / 0.4) + (5 if ep["clears_6m_high"] else 0)
    p["tradeable"] = (5 * _clip01((ep["adr_before"] - 0.02) / 0.03)
                      + 5 * _clip01(math.log10(max(dollar_vol, 1) / 1e7) / 0.7))
    return {"score": round(sum(p.values())), "parts": {k: round(x, 1) for k, x in p.items()}}


# ---------------------------------------------------------------------------
# Trade plan
# ---------------------------------------------------------------------------
def grade(score: float) -> str:
    return "A+" if score >= 85 else "A" if score >= 75 else "B" if score >= 62 else "C"


def plan(entry: float, stop_ref: float, adr: float, cfg: dict, size_factor: float,
         fixed_stop: bool = False) -> dict:
    """Entry, stop and size.

    Before a trigger the low of the day is unknown, so the stop is planned from the
    recent lows, never closer than 0.4 and never wider than 1.0 average daily ranges.
    Size is set so a stop-out costs the configured share of the account.
    """
    a = cfg["account"]
    adr_usd = adr * entry
    dist = entry - stop_ref
    wide = fixed_stop and dist > 1.0 * adr_usd
    if not fixed_stop:
        dist = min(max(dist, 0.4 * adr_usd), 1.0 * adr_usd)
    stop = entry - dist
    risk_pct = dist / entry * 100
    risk_usd = a["size_usd"] * a["risk_per_trade_pct"] / 100 * size_factor
    shares = math.floor(risk_usd / dist) if dist > 0 else 0
    cap_shares = math.floor(a["size_usd"] * a["max_position_pct"] / 100 / entry)
    capped = shares > cap_shares
    shares = min(shares, cap_shares)
    return {
        "entry": round(entry, 2), "stop": round(stop, 2), "risk_pct": round(risk_pct, 1),
        "stop_adr": round(dist / adr_usd, 2) if adr_usd else None,
        "shares": int(shares), "position_pct": round(shares * entry / a["size_usd"] * 100, 1),
        "risk_usd": round(shares * dist), "capped": bool(capped), "stop_too_wide": bool(wide),
    }

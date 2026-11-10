"""Replay the scanner over history and record what every flagged setup did next.

    python -m backtest.run --days 1300      # live data, writes bt_out/
    python -m backtest.run --demo           # invented data, for a smoke test

For each past session the replay sees only bars up to that session's close, flags
setups with the same detection code the daily sheet uses, then follows each one
forward with a fixed set of trading rules:

  * entry   the next session, if price trades through the trigger (at the trigger,
            or at the open if it gaps above it)
  * stop    planned from the last three lows, between 0.4 and 1.0 average daily ranges
  * exits   stop; sell half on the first profitable close from day 3 and move the
            stop to the entry; sell the rest on the first close under the 10-day average

Results are in R (multiples of the amount risked). Known limits: daily bars cannot
show whether the low or the breakout came first on the entry day, and the stock list
is today's listings, so companies that were delisted are missing.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scanner import config as C
from scanner import setups

WARMUP = 130          # sessions needed before the first replay day
PARTIAL_DAY = 3
MAX_HOLD = 60


def _log(msg: str) -> None:
    print(f"[backtest] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Trade simulation for one stock
# ---------------------------------------------------------------------------
def walk(o, h, l, c, ma, t: int, entry: float, stop: float, intraday: bool, *,
         partial: str | None = "day3", target: float | None = None,
         max_hold: int = MAX_HOLD) -> dict:
    """One trade under one set of exit rules. Result in R (multiples of entry - stop).

    partial  "day3": sell half on the first profitable close from day 3, stop to entry
             "2R":   sell half at +2R, stop to entry
    target   sell everything at this many R
    ma       sell what is left on the first close under this moving average
    Same-day ties between the stop and a target go to the stop.
    """
    T = len(c)
    risk = entry - stop
    exit_r = half_r = exit_day = None
    stop_now = stop
    ambiguous = False
    goal = entry + target * risk if target else None
    two_r = entry + 2 * risk

    if intraday:
        if l[t] <= stop:
            if c[t] < entry:                 # broke out and reversed through the stop
                exit_r, exit_day = -1.0, t
            else:                            # the low most likely came before the breakout
                ambiguous = True
        if exit_r is None:
            if goal is not None and h[t] >= goal:
                exit_r, exit_day = float(target), t
            elif partial == "2R" and h[t] >= two_r:
                half_r, stop_now = 2.0, entry
    day = t + 1
    last = min(T - 1, t + max_hold)
    while exit_r is None and day <= last:
        if np.isfinite(c[day]):
            if o[day] <= stop_now:
                exit_r, exit_day = (o[day] - entry) / risk, day
            elif l[day] <= stop_now:
                exit_r, exit_day = (stop_now - entry) / risk, day
            else:
                if goal is not None and h[day] >= goal:
                    exit_r, exit_day = (max(goal, o[day]) - entry) / risk, day
                else:
                    if half_r is None:
                        if partial == "day3" and day - t >= PARTIAL_DAY and c[day] > entry:
                            half_r, stop_now = (c[day] - entry) / risk, max(stop_now, entry)
                        elif partial == "2R" and h[day] >= two_r:
                            half_r, stop_now = (max(two_r, o[day]) - entry) / risk, max(stop_now, entry)
                    if ma is not None and np.isfinite(ma[day]) and c[day] < ma[day]:
                        exit_r, exit_day = (c[day] - entry) / risk, day
        day += 1
    complete = exit_r is not None
    if exit_r is None:
        if t + max_hold <= T - 1:            # held the maximum time
            exit_r, exit_day, complete = (c[t + max_hold] - entry) / risk, t + max_hold, True
        else:                                # history ends before the trade does
            k = T - 1
            while k > t and not np.isfinite(c[k]):
                k -= 1
            exit_r, exit_day = (c[k] - entry) / risk, k
    r = exit_r if half_r is None else 0.5 * half_r + 0.5 * exit_r
    return {"r": r, "hold": exit_day - t, "complete": complete, "ambiguous": ambiguous,
            "took_half": half_r is not None}


def simulate(o, h, l, c, s10, t: int, entry: float, stop: float, intraday: bool,
             s20=None) -> dict | None:
    """Follow one trade. `t` is the entry session; `intraday` means the entry happened
    during session t (a trigger), otherwise at its close. The sheet's own rules give
    `r`; the other columns are the same trade under different exit rules."""
    T = len(c)
    risk = entry - stop
    if not np.isfinite(risk) or risk <= 0 or not np.isfinite(entry):
        return None
    out = {"entry": entry, "stop": stop, "risk_pct": risk / entry}
    main = walk(o, h, l, c, s10, t, entry, stop, intraday, partial="day3")
    out.update(main, r_pess=-1.0 if main["ambiguous"] else main["r"])

    variants = {
        "r_ma10": dict(ma=s10, partial=None),
        "r_p2": dict(ma=s10, partial="2R"),
        "r_t2": dict(ma=None, partial=None, target=2.0, max_hold=20),
        "r_t3": dict(ma=None, partial=None, target=3.0, max_hold=30),
    }
    if s20 is not None:
        variants["r_ma20"] = dict(ma=s20, partial=None)
        variants["r_h20"] = dict(ma=s20, partial="day3")
    for name, kw in variants.items():
        ma = kw.pop("ma")
        w = walk(o, h, l, c, ma, t, entry, stop, intraday, **kw)
        out[name] = w["r"] if w["complete"] else np.nan
        out[name.replace("r_", "hold_")] = w["hold"]
    # the same trade with half the stop distance (so twice the size for the same risk)
    w = walk(o, h, l, c, s10, t, entry, entry - 0.5 * risk, intraday, partial="day3")
    out["r_tight"] = w["r"] if w["complete"] else np.nan
    out["amb_tight"] = w["ambiguous"]

    # Did it reach +2R before the stop, within 20 sessions? Same-day ties go to the stop.
    target = entry + 2 * risk
    hit = False
    first = t if intraday else t + 1
    for d in range(first, min(T, t + 21)):
        if not np.isfinite(c[d]):
            continue
        stopped = l[d] <= stop and not (d == t and c[t] >= entry)
        if stopped:
            break
        if h[d] >= target:
            hit = True
            break
    out["hit2r"] = hit
    hi = h[first:min(T, t + 11)]
    out["mfe10_r"] = (np.nanmax(hi) - entry) / risk if len(hi) and np.isfinite(hi).any() else np.nan
    for n in (5, 10, 20):
        out[f"ret{n}"] = c[t + n] / entry - 1 if t + n < T and np.isfinite(c[t + n]) else np.nan
    return out


# ---------------------------------------------------------------------------
# Whole-market panels, one value per session and stock
# ---------------------------------------------------------------------------
def panels(bars: dict[str, pd.DataFrame], meta: pd.DataFrame, cfg: dict) -> dict:
    skip = set(C.INDEXES) | set(C.GROUPS)
    cols = [t for t in bars["Close"].columns if t not in skip]
    o, h, l, c, v = (bars[f][cols].astype("float64") for f in ("Open", "High", "Low", "Close", "Volume"))
    u = cfg["universe"]

    rng = h / l - 1
    adr = rng.rolling(20).mean()
    dvol = (c * v).rolling(20).mean()
    sma10, sma20, sma50 = (c.rolling(n).mean() for n in (10, 20, 50))
    sma200 = c.rolling(200).mean()
    nbars = c.notna().cumsum()
    liquid = (c >= u["min_price"]) & (dvol >= u["min_dollar_volume"]) & (nbars >= 60) & c.notna() & v.notna()
    p1m, p3m, p6m = c / c.shift(21) - 1, c / c.shift(63) - 1, c / c.shift(126) - 1
    rs_parts = [p.where(liquid).rank(axis=1, pct=True) * 100 for p in (p1m, p3m, p6m)]
    rs = pd.DataFrame(np.fmax(np.fmax(rs_parts[0].to_numpy(), rs_parts[1].to_numpy()),
                              rs_parts[2].to_numpy()), index=c.index, columns=c.columns)
    leader = liquid & (adr * 100 >= u["min_adr_pct"]) & (rs >= u["min_rs_rank"]) & (c > sma50)

    # --- market regime, per session -------------------------------------------------
    daily = pd.DataFrame(index=c.index)
    for t in C.INDEXES:
        s = bars["Close"][t]
        m10, m20, m50 = (s.rolling(n).mean() for n in (10, 20, 50))
        up10, up20 = m10 > m10.shift(3), m20 > m20.shift(5)
        green = (s > m20) & (m10 > m20) & up10 & up20
        red = (m10 < m20) & (s < m20)
        k = t.lower()
        daily[f"{k}_state"] = np.where(green, "green", np.where(red, "red", "amber"))
        daily[f"{k}_vs10"], daily[f"{k}_vs20"], daily[f"{k}_vs50"] = s / m10 - 1, s / m20 - 1, s / m50 - 1
        daily[f"{k}_10over20"], daily[f"{k}_up10"], daily[f"{k}_up20"] = m10 > m20, up10, up20
        daily[f"{k}_chg"] = s / s.shift(1) - 1
    n_liq = liquid.sum(axis=1).clip(lower=1)
    daily["n_liquid"], daily["n_leaders"] = liquid.sum(axis=1), leader.sum(axis=1)
    daily["above20"] = ((c > sma20) & liquid).sum(axis=1) / n_liq * 100
    daily["above50"] = ((c > sma50) & liquid).sum(axis=1) / n_liq * 100
    chg = c / c.shift(1) - 1
    volup = v > v.rolling(50).mean()
    daily["up4"] = ((chg >= 0.04) & volup & liquid).sum(axis=1)
    daily["down4"] = ((chg <= -0.04) & volup & liquid).sum(axis=1)
    daily["new_highs"] = ((h >= h.rolling(252, min_periods=120).max()) & liquid).sum(axis=1)
    daily["new_lows"] = ((l <= l.rolling(252, min_periods=120).min()) & liquid).sum(axis=1)
    # share of breakouts from 3 to 10 sessions ago that are still above their breakout close
    fired = (c > h.rolling(50).max().shift(1)) & (v > 1.5 * v.rolling(50).mean().shift(1))
    first = fired & ~(fired.shift(1).rolling(10, min_periods=1).max().fillna(0) > 0)
    wins = total = 0
    for k in range(3, 11):
        was = first.shift(k).fillna(False).astype(bool) & liquid
        total = total + was.sum(axis=1)
        wins = wins + (was & (c > c.shift(k))).sum(axis=1)
    daily["ft_n"] = total
    daily["ft"] = (wins / total.where(total >= 10) * 100)

    # --- candidate factors -----------------------------------------------------------
    q = bars["Close"]["QQQ"]
    qdd = q / bars["High"]["QQQ"].rolling(20).max() - 1
    sdd = c / h.rolling(20).max() - 1
    pull_rs = sdd.sub(qdd, axis=0)                      # holding up better than the index
    ratio = c.div(q, axis=0)
    rsline = ratio / ratio.rolling(60).max()            # 1.0 = relative-strength line at its high
    ext50 = (c / sma50 - 1) / adr                       # distance above the 50-day, in daily ranges
    ext200 = c / sma200 - 1
    # consecutive sessions above the 50-day (trend maturity)
    a = (c > sma50).to_numpy()
    streak = np.zeros(a.shape, dtype=np.int32)
    for i in range(1, a.shape[0]):
        streak[i] = np.where(a[i], streak[i - 1] + 1, 0)
    up_vol = (v.where(chg > 0, 0.0)).rolling(20).sum()
    dn_vol = (v.where(chg < 0, 0.0)).rolling(20).sum()
    ud_vol = up_vol / dn_vol.replace(0, np.nan)
    off_high = c / h.rolling(252, min_periods=120).max() - 1

    # --- group strength --------------------------------------------------------------
    info = meta.drop_duplicates("symbol").set_index("symbol").reindex(cols)
    ind = info["industry"].fillna("").astype(str).to_numpy()
    sec = info["sector"].fillna("").astype(str).to_numpy()
    ind_code, ind_names = pd.factorize(ind)
    sec_code, _ = pd.factorize(sec)
    known = ind != ""
    L = leader.to_numpy()
    Q = liquid.to_numpy()
    blend = (0.6 * p1m + 0.4 * p3m).to_numpy()
    T, N = L.shape
    ind_leaders = np.zeros((T, N), dtype=np.int16)
    sec_leaders = np.zeros((T, N), dtype=np.int16)
    ind_share = np.full((T, N), np.nan)
    ind_rank = np.full((T, N), np.nan)
    n_ind, n_sec = len(ind_names), int(sec_code.max()) + 1
    for i in range(T):
        lead_i = L[i] & known
        cnt = np.bincount(ind_code[lead_i], minlength=n_ind)
        liq_cnt = np.bincount(ind_code[Q[i] & known], minlength=n_ind)
        ind_leaders[i] = np.where(known, cnt[ind_code], 0)
        sec_leaders[i] = np.where(known, np.bincount(sec_code[lead_i], minlength=n_sec)[sec_code], 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            ind_share[i] = np.where(known & (liq_cnt[ind_code] >= 4), cnt[ind_code] / liq_cnt[ind_code], np.nan)
        ok = Q[i] & known & np.isfinite(blend[i])
        if ok.sum() > 50:
            med = pd.Series(blend[i][ok]).groupby(ind_code[ok]).agg(["median", "size"])
            med = med[med["size"] >= 4]["median"]
            if len(med) >= 5:
                pct = med.rank(pct=True) * 100
                lookup = np.full(n_ind, np.nan)
                lookup[pct.index.to_numpy()] = pct.to_numpy()
                ind_rank[i] = np.where(known, lookup[ind_code], np.nan)

    F = np.asfortranarray
    return {
        "cols": cols, "index": c.index, "daily": daily, "industry": ind, "sector": sec,
        "O": F(o.to_numpy()), "H": F(h.to_numpy()), "L": F(l.to_numpy()), "C": F(c.to_numpy()),
        "V": F(v.to_numpy()), "S10": F(sma10.to_numpy()), "S20": F(sma20.to_numpy()),
        "leader": L, "liquid": Q, "rs": rs.to_numpy(),
        "rs1": rs_parts[0].to_numpy(), "rs3": rs_parts[1].to_numpy(), "rs6": rs_parts[2].to_numpy(),
        "dvol": dvol.to_numpy(), "adr": adr.to_numpy(), "p6m": p6m.to_numpy(),
        "gap": (o / c.shift(1) - 1).to_numpy(),
        "pull_rs": pull_rs.to_numpy(), "rsline": rsline.to_numpy(), "ext50": ext50.to_numpy(),
        "ext200": ext200.to_numpy(), "streak50": streak, "ud_vol": ud_vol.to_numpy(),
        "off_high": off_high.to_numpy(),
        "ind_leaders": ind_leaders, "sec_leaders": sec_leaders, "ind_share": ind_share,
        "ind_rank": ind_rank,
    }


FACTORS = ("pull_rs", "rsline", "ext50", "ext200", "streak50", "ud_vol", "off_high", "p6m",
           "ind_leaders", "sec_leaders", "ind_share", "ind_rank", "rs1", "rs3", "rs6")


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def replay(bars: dict[str, pd.DataFrame], meta: pd.DataFrame, cfg: dict,
           start: int = WARMUP) -> tuple[pd.DataFrame, pd.DataFrame]:
    P = panels(bars, meta, cfg)
    O, H, L, Cl, V, S10, S20 = P["O"], P["H"], P["L"], P["C"], P["V"], P["S10"], P["S20"]
    T, N = Cl.shape
    cols, dates = P["cols"], P["index"]
    e = cfg["episodic_pivot"]
    rows: list[dict] = []
    fired_days: dict[int, list[int]] = {}
    busy = {"pending": {}, "fired": {}}      # stock -> session its open trade exits
    t0 = time.time()

    def common(d: int, j: int) -> dict:
        r = {"date": dates[d].strftime("%Y-%m-%d"), "ticker": cols[j], "d": d,
             "industry": P["industry"][j], "sector": P["sector"][j],
             "price": Cl[d, j], "rs": P["rs"][d, j], "dollar_vol": P["dvol"][d, j],
             "base_count": sum(1 for x in fired_days.get(j, ()) if d - 126 <= x < d)}
        for k in FACTORS:
            r[k] = P[k][d, j]
        # forward returns from the next open: no trading rules involved
        if d + 1 < T and np.isfinite(O[d + 1, j]):
            for n in (5, 10, 20):
                r[f"fwd{n}"] = Cl[d + n, j] / O[d + 1, j] - 1 if d + n < T else np.nan
        return r

    def pending_trade(d: int, j: int, pivot: float, adr: float, row: dict) -> None:
        """Entry the next session if price trades through the trigger."""
        o, h, l, c = O[:, j], H[:, j], L[:, j], Cl[:, j]
        level = pivot * 1.001
        days_to = np.nan
        for k in range(1, 6):
            if d + k < T and np.isfinite(h[d + k]) and h[d + k] >= level:
                days_to = k
                break
        row["trig_days"] = days_to
        row["trig_next"] = days_to == 1
        if d + 1 < T and np.isfinite(o[d + 1]):
            row["gap_open"] = o[d + 1] / pivot - 1
        if days_to != 1 or busy["pending"].get(j, -1) >= d + 1:
            return
        entry = max(level, o[d + 1])
        ref = np.nanmin(l[d - 2:d + 1])
        dist = min(max(entry - ref, 0.4 * adr * entry), 1.0 * adr * entry)
        sim = simulate(o, h, l, c, S10[:, j], d + 1, entry, entry - dist, intraday=True,
                       s20=S20[:, j])
        if sim:
            row.update(sim)
            row["traded"] = True
            busy["pending"][j] = d + 1 + sim["hold"]

    for d in range(start, T):
        # --- episodic pivots: any liquid stock with a big recent gap
        ep_names = set()
        lo = max(0, d - e["lookback_days"])
        gap_max = np.nanmax(np.where(np.isfinite(P["gap"][lo:d + 1]), P["gap"][lo:d + 1], -1), axis=0)
        for j in np.flatnonzero(P["liquid"][d] & (gap_max >= e["min_gap_pct"] / 100)):
            arr = (O[:, j], H[:, j], L[:, j], Cl[:, j], V[:, j])
            try:
                ep = setups.detect_ep(*arr, d, cfg)
            except Exception:
                ep = None
            if ep is None:
                continue
            ep_names.add(j)
            sc = setups.score_ep(ep, P["dvol"][d, j])
            row = {**common(d, j), "kind": "ep", "status": "today" if ep["age"] == 0 else "holding",
                   "score": sc["score"], **{f"s_{k}": x for k, x in sc["parts"].items()},
                   "pivot": ep["pivot"], "adr": ep["adr"]}
            for k in ("age", "gap", "vol_mult", "close_pos", "prior_3m", "clears_6m_high",
                      "adr_before", "dist", "gap_day_gain", "pre_close", "pre_dollar_vol"):
                row[f"e_{k}"] = ep[k]
            pending_trade(d, j, ep["pivot"], ep["adr"], row)
            rows.append(row)

        # --- breakouts: leaders only
        for j in np.flatnonzero(P["leader"][d]):
            if j in ep_names:
                continue
            arr = (O[:, j], H[:, j], L[:, j], Cl[:, j], V[:, j])
            try:
                hit = setups.detect_breakout_today(*arr, d, cfg)
                base = None if hit else setups.detect_base(*arr, d, cfg)
            except Exception:
                continue
            if hit is None and base is None:
                continue
            b = hit or base
            sc = setups.score_base(b, P["rs"][d, j], P["dvol"][d, j])
            row = {**common(d, j), "kind": "fired" if hit else "base",
                   "status": "fired" if hit else setups.base_status(base),
                   "score": sc["score"], **{f"s_{k}": x for k, x in sc["parts"].items()},
                   "pivot": b["pivot"], "adr": b["adr"]}
            for k in ("leg_gain", "leg_days", "base_days", "depth", "retrace", "tight", "close_span",
                      "higher_lows", "holding", "vol_ratio", "ma_stack", "ma_ok", "ma_dist", "dist"):
                row[f"b_{k}"] = b[k]
            if hit:
                for k in ("vol_mult", "extension", "close_pos", "day_gain"):
                    row[f"f_{k}"] = hit[k]
                stop = hit["low_of_day"]
                row["wide"] = (hit["close"] - stop) > hit["adr"] * hit["close"]
                if busy["fired"].get(j, -1) < d:
                    sim = simulate(*arr[:4], S10[:, j], d, hit["close"], stop, intraday=False,
                                   s20=S20[:, j])
                    if sim:
                        row.update(sim)
                        row["traded"] = True
                        busy["fired"][j] = d + sim["hold"]
                fired_days.setdefault(j, []).append(d)
            else:
                pending_trade(d, j, base["pivot"], base["adr"], row)
            rows.append(row)
        if (d - start) % 100 == 0:
            _log(f"session {d - start + 1} of {T - start}, {len(rows)} setups so far, "
                 f"{time.time() - t0:.0f}s")

    out = pd.DataFrame(rows)
    daily = P["daily"].iloc[start:].copy()
    daily.insert(0, "date", daily.index.strftime("%Y-%m-%d"))
    return out, daily.reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1300, help="calendar days of history")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out", default="bt_out")
    args = ap.parse_args()
    cfg = C.load()
    health = {}
    if args.demo:
        from scanner import demo
        bars, meta = demo.make()
        source = "invented demo data"
    else:
        from scanner import fetch, universe
        # No price pre-filter here: a stock that is cheap today may have been a leader then
        meta, source = universe.load(C.DATA / "universe.json", 0, 0)
        tickers = list(C.INDEXES) + list(C.GROUPS) + meta["symbol"].tolist()
        bars, health = fetch.fetch_bars(tickers, args.days)
        if not bars or "QQQ" not in bars["Close"]:
            raise RuntimeError("Price download failed.")
        bars = fetch.align(bars)
    _log(f"{bars['Close'].shape[0]} sessions x {bars['Close'].shape[1]} stocks")
    rows, daily = replay(bars, meta, cfg)
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    rows.to_csv(out / "rows.csv.gz", index=False, float_format="%.5g")
    daily.to_csv(out / "daily.csv", index=False, float_format="%.5g")
    info = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "first": daily["date"].iloc[0], "last": daily["date"].iloc[-1],
            "sessions": len(daily), "stocks": int(bars["Close"].shape[1]), "rows": len(rows),
            "stock_list": source, **health,
            "rules": {"partial_day": PARTIAL_DAY, "max_hold": MAX_HOLD, "warmup": WARMUP}}
    (out / "meta.json").write_text(json.dumps(info, indent=1))
    _log(json.dumps(info))


if __name__ == "__main__":
    main()

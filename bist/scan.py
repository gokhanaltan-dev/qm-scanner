"""End-of-day scan with the frozen V1 rules.

    python -m bist.scan --data DATA_DIR --out OUT_DIR [--prev OUT_DIR/scan.json]

Writes scan.json (machine-readable, with every measurement) and charts for the best candidates.
A data outage raises an error; it never produces an empty shortlist.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

from . import core


def _f(x, nd=4):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def candidate(mk, a, f, tk, state, P, base_low, arm_i, cfg, extra=None):
    i = len(a["C"]) - 1
    d = pd.DatetimeIndex(a["dates"])
    atr = f["atr"][i]
    adj = a["adj"][i]                      # 1.0 on the latest bar unless the vendor rebased
    to_try = lambda x: x / adj
    conf = P + cfg["confirmation"]["close_buffer_atr"] * atr
    max_entry = min(P * (1 + cfg["execution"]["max_entry_pct"]), P + cfg["execution"]["max_entry_atr"] * atr)
    stop = f["low_n"][i] - cfg["execution"]["stop_buffer_atr"] * atr
    m = core.setup_measurements(a, f, i, P, arm_i, cfg)
    sc = core.score_setup(m, cfg)
    H = a["H"]
    look = H[max(0, i - 251):i + 1]
    above = look[look > P * 1.0005]
    overhead = float(above.min()) if len(above) else None
    vol_need = cfg["confirmation"]["volume_multiple"] * f["volavg"][i]
    lot_px = a["C"][i]
    row = {
        "ticker": tk, "name": mk.meta["name"].get(tk, ""), "sector": mk.meta["sector"].get(tk, "Unknown"),
        "state": state, "pattern": "tight horizontal base (V1)", "as_of": str(d[i].date()),
        "close": _f(to_try(a["C"][i])), "pivot": _f(to_try(P)), "base_low_invalidation": _f(to_try(base_low)),
        "armed_on": str(d[arm_i].date()), "base_window": [str(d[arm_i - cfg['base']['window'] + 1].date()), str(d[arm_i].date())],
        "sessions_since_armed": int(i - arm_i), "sessions_left_before_expiry": int(cfg["base"]["expiry_sessions"] - (i - arm_i)),
        "atr14": _f(to_try(atr)), "dist_pct": _f((P - a["C"][i]) / P * 100, 2), "dist_atr": _f((P - a["C"][i]) / atr, 2),
        "confirm_close_at_or_above": core.round_tick(to_try(conf), cfg, "up"),
        "required_volume_shares": int(round(vol_need * adj)), "required_turnover_try_approx": _f(vol_need * lot_px, 0),
        "avg_volume_20_shares": int(round(f["volavg"][i] * adj)),
        "max_entry": core.round_tick(to_try(max_entry), cfg, "down"),
        "initial_stop_reference": core.round_tick(to_try(stop), cfg, "down"),
        "stop_distance_pct_from_confirm": _f((conf - stop) / conf * 100, 2),
        "overhead_resistance_252": _f(to_try(overhead)) if overhead else None,
        "illustrative_2R_from_confirm": _f(to_try(conf + 2 * (conf - stop))),
        "rs_percentile": _f(a["rs_pct"][i], 1), "liquidity_rank": _f(a["liq_rank"][i], 0),
        "median_turnover_60_try": None, "measurements": {k: _f(v) for k, v in m.items()},
        "score": sc, "base_depth_pct": _f(f["depth"][arm_i] * 100, 2), "contraction_ratio": _f(f["contr"][arm_i], 3),
        "volume_dryup_ratio": _f(f["volratio"][arm_i], 3),
        "locked_or_single_print_sessions_last_20": int((a["H"][i - 19:i + 1] <= a["L"][i - 19:i + 1]).sum()),
        "research_status": "untested hypothesis until the V1 backtest is read; see results.json",
    }
    if extra:
        row.update(extra)
    return row


def funnel(mk, xs, feats, cfg):
    """How many universe members pass each rule on the latest session."""
    c = {"universe": 0, "trend_ok": 0, "rs_ge_80": 0, "trend_and_rs": 0, "depth_ok": 0, "touch_ok": 0,
         "contraction_ok": 0, "volume_dryup_ok": 0, "structure_all": 0, "all_rules": 0, "all_rules_within_2atr_5pct": 0}
    b = cfg["base"]
    for tk, (a, f) in feats.items():
        i = len(a["C"]) - 1
        if a["dates"][i] != mk.dates.values[-1] or not a["member"][i]:
            continue
        c["universe"] += 1
        tr = bool(f["trend_ok"][i]); rs = bool(np.nan_to_num(a["rs_pct"][i], nan=-1) >= cfg["relative_strength"]["min_percentile"])
        c["trend_ok"] += tr; c["rs_ge_80"] += rs; c["trend_and_rs"] += tr and rs
        c["depth_ok"] += bool(f["depth"][i] <= b["max_depth"]); c["touch_ok"] += bool(f["touch_ok"][i])
        c["contraction_ok"] += bool(f["contr"][i] <= b["max_contraction_ratio"])
        c["volume_dryup_ok"] += bool(f["volratio"][i] <= b["max_volume_ratio"])
        c["structure_all"] += bool(f["struct_ok"][i])
        allr = tr and rs and bool(f["struct_ok"][i])
        c["all_rules"] += allr
        dist = f["P"][i] - a["C"][i]
        c["all_rules_within_2atr_5pct"] += bool(allr and dist <= 2 * f["atr"][i] and dist / f["P"][i] <= 0.05)
    return c


def chart(a, f, row, path, n=180):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    i = len(a["C"])
    s = slice(max(0, i - n), i)
    d = pd.DatetimeIndex(a["dates"][s])
    x = np.arange(len(d))
    adj = a["adj"][i - 1]
    fig, (ax, av) = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True, gridspec_kw={"height_ratios": [3.2, 1]})
    up = a["C"][s] >= np.r_[a["C"][s][0], a["C"][s][:-1]]
    ax.vlines(x, a["L"][s] / adj, a["H"][s] / adj, color=np.where(up, "#2a7f62", "#b5483c"), lw=1)
    ax.scatter(x, a["C"][s] / adj, s=5, color=np.where(up, "#2a7f62", "#b5483c"), zorder=3)
    for key, lab, col in (("sma50", "SMA50", "#3b6fb6"), ("sma200", "SMA200", "#8a6fb0"), ("ema20", "EMA20", "#c98a1a")):
        ax.plot(x, f[key][s] / adj, lw=1.1, color=col, label=lab)
    ax.axhline(row["pivot"], color="#222", ls="--", lw=1, label=f"pivot {row['pivot']:.2f}")
    ax.axhline(row["base_low_invalidation"], color="#b5483c", ls=":", lw=1.2, label=f"invalidation {row['base_low_invalidation']:.2f}")
    ax.set_title(f"{row['ticker']}  {row['state']}  close {row['close']:.2f} on {row['as_of']}  (prices adjusted for capital actions and dividends)", fontsize=10)
    ax.legend(fontsize=8, loc="upper left", ncol=5, frameon=False)
    ax.grid(alpha=.25)
    av.bar(x, a["V"][s] * adj / 1e6, color=np.where(up, "#2a7f62", "#b5483c"), width=0.8)
    av.plot(x, f["volavg"][s] * adj / 1e6, color="#222", lw=1, label="20-session average")
    av.set_ylabel("volume, m shares", fontsize=8); av.legend(fontsize=8, frameon=False, loc="upper left"); av.grid(alpha=.25)
    step = max(1, len(d) // 8)
    av.set_xticks(x[::step]); av.set_xticklabels([t.strftime("%d %b %y") for t in d[::step]], fontsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--prev", default=None)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--history", default=None, help="jsonl file; one line per scanned session is appended")
    a_ = ap.parse_args()
    os.makedirs(a_.out, exist_ok=True)
    cfg = core.load_config()
    mk = core.build_market(a_.data, cfg, as_of=a_.as_of)
    q = mk.quality
    if q["tickers_with_prices"] < 300:
        raise SystemExit("DATA BLOCKED: too few price histories; this is a data outage, not an empty shortlist.")
    xs = core.cross_section(mk, cfg)
    last = mk.dates[-1]
    feats, cands, events, forming = {}, [], [], []
    n_member_missing_open = 0
    for tk in mk.panels["C"].columns:
        a = core.stock_arrays(mk, xs, tk)
        if a is None or len(a["C"]) < 60:
            continue
        f = core.features(a["O"], a["H"], a["L"], a["C"], a["V"], cfg)
        feats[tk] = (a, f)
        if len(a["C"]) < 260 or a["dates"][-1] != last.to_datetime64():
            continue
        r = core.run_tight_base(a, f, cfg, tk)
        i = len(a["C"]) - 1
        d = pd.DatetimeIndex(a["dates"])
        if r["active"]:
            s = r["active"]
            state = "UNCONFIRMED CROSS" if s.get("crossed_i") == i else "ARMED / NEAR BREAKOUT"
            cands.append(candidate(mk, a, f, tk, state, s["P"], s["base_low"], s["armed_i"], cfg, {"setup_id": s["id"]}))
        if r["pending_signal"]:
            t = r["pending_signal"]
            s = [x for x in r["setups"] if x["id"] == t["setup_id"]][0]
            cands.append(candidate(mk, a, f, tk, "BREAKOUT CONFIRMED", s["P"], s["base_low"], s["armed_i"], cfg,
                                   {"setup_id": s["id"], "volume_multiple_on_signal": _f(t["vol_mult"], 2),
                                    "note": "Signal exists from this close. Model entry is the next open, only if above "
                                            "the pivot and at or below the maximum entry."}))
        for s in r["setups"]:
            age = i - s["end_i"]
            if age == 0 and s["state"] in ("INVALIDATED", "EXPIRED"):
                events.append({"ticker": tk, "setup_id": s["id"], "state": s["state"], "why": s["why"], "pivot": _f(s["P"])})
            if 0 < age <= 5 and s["state"] == "CONFIRMED":
                mx = min(s["P"] * 1.03, s["P"] + 0.75 * f["atr"][s["end_i"] - 1])
                st = "EXTENDED / DO NOT CHASE" if a["C"][i] > mx else "BREAKOUT CONFIRMED (earlier session)"
                events.append({"ticker": tk, "setup_id": s["id"], "state": st, "confirmed_on": str(d[s["end_i"]].date()),
                               "pivot": _f(s["P"]), "close": _f(a["C"][i]), "max_entry": _f(mx),
                               "entry_outcome": s.get("trade_kind")})
        if not r["active"] and r["forming_last"] and not r["busy"]:
            row = candidate(mk, a, f, tk, "FORMING", f["P"][i], f["base_low"][i], i, cfg)
            row["note"] = "Pivot not frozen yet: it freezes when the close is within 1 ATR of the pivot."
            forming.append(row)

    cands.sort(key=lambda r: -r["score"]["total"])
    forming.sort(key=lambda r: -r["score"]["total"])
    shortlist = (cands + forming)[:8]

    # Exploratory group: listings younger than 252 sessions that would rank in the top 200 by liquidity.
    young = []
    med = xs["med60"].iloc[-1]
    cut = med.where(xs["member"].iloc[-1]).min()
    for tk in mk.panels["C"].columns:
        if bool(xs["young"][tk].iloc[-1]) and np.isfinite(med.get(tk, np.nan)) and med[tk] >= cut and tk in feats:
            a, f = feats[tk]
            i = len(a["C"]) - 1
            young.append({"ticker": tk, "sessions": int(len(a["C"])), "median_turnover_60_try": _f(med[tk], 0),
                          "structure_rules_pass": bool(f["struct_ok"][i]) if i >= cfg["base"]["window"] else False,
                          "dist_atr": _f((f["P"][i] - a["C"][i]) / f["atr"][i], 2) if np.isfinite(f["atr"][i]) else None})

    from .backtest import market_regime
    reg = market_regime(mk, xs).iloc[-1]
    x = mk.xu100
    sec = mk.meta["sector"].reindex(mk.panels["C"].columns)
    Cf = mk.panels["C"].ffill()
    r63 = (Cf.iloc[-1] / Cf.iloc[-64] - 1).where(xs["member"].iloc[-1])
    sector_tbl = (pd.DataFrame({"sector": sec, "r63": r63}).dropna().groupby("sector")["r63"]
                  .agg(["median", "count"]).sort_values("median", ascending=False))
    context = {
        "xu100_close": _f(x.iloc[-1], 2), "xu100_sma50": _f(reg["xu_sma50"], 2), "xu100_sma200": _f(reg["xu_sma200"], 2),
        "xu100_above_sma50": bool(reg["xu_above_50"]), "xu100_sma50_above_sma200": bool(reg["xu_sma50"] > reg["xu_sma200"]),
        "xu100_return_21": _f(x.iloc[-1] / x.iloc[-22] - 1), "xu100_return_63": _f(x.iloc[-1] / x.iloc[-64] - 1),
        "xu100_return_126": _f(x.iloc[-1] / x.iloc[-127] - 1), "xu100_vs_252_high": _f(x.iloc[-1] / x.iloc[-252:].max() - 1),
        "breadth_pct_universe_above_sma50": _f(reg["breadth50"] * 100, 1),
        "sector_median_63_session_return_universe": [{"sector": k, "median_return": _f(v["median"]), "shares": int(v["count"])}
                                                      for k, v in sector_tbl.iterrows()],
        "xu100_63_session_return_for_comparison": _f(x.iloc[-1] / x.iloc[-64] - 1),
    }
    prov = q["provenance"]
    mem = xs["member"].iloc[-1]
    out = {
        "version": cfg["version"],
        "status": {
            "data_sources": {k: v.get("source") for k, v in prov["sources"].items() if isinstance(v, dict) and v.get("source")},
            "retrieved_istanbul": prov["retrieved_istanbul_end"], "latest_completed_session": str(last.date()),
            "incomplete_bar_dropped": q["incomplete_bar_dropped"], "prices_delayed": prov["delay_note"],
            "coverage": {"listed_ordinary_shares": prov["sources"]["universe"]["stocks"],
                         "price_histories": q["tickers_with_prices"],
                         "failed_downloads": prov["sources"]["isy_prices"]["failed"],
                         "universe_members_today": int(mem.sum()),
                         "universe_members_without_verified_open_today": int(((mk.panels["OPEN_OK"].iloc[-1] < 0.5) & mem).sum()),
                         "open_price_verified_share_all_bars": _f(q["open_verified_share"], 4)},
            "limitations": ["VBTS, gross-settlement and single-price status are not in the price data; checked by hand for shortlisted names only.",
                            "Prices are adjusted for capital actions and dividends by the vendor (Is Yatirim); levels shown are in today's TRY."],
        },
        "market_context": context, "funnel": funnel(mk, xs, feats, cfg),
        "shortlist": shortlist, "armed_or_confirmed_count": len(cands), "forming_count": len(forming),
        "events_today_or_recent": events, "exploratory_young_listings": young,
    }
    if a_.prev and os.path.exists(a_.prev):
        with open(a_.prev, encoding="utf-8") as fh:
            prev = json.load(fh)
        was = {r.get("setup_id") or r["ticker"]: r["state"] for r in prev.get("shortlist", [])}
        now = {r.get("setup_id") or r["ticker"]: r["state"] for r in shortlist}
        out["changes_since_previous"] = {"previous_session": prev["status"]["latest_completed_session"],
                                         "new": [k for k in now if k not in was],
                                         "state_changed": {k: [was[k], now[k]] for k in now if k in was and was[k] != now[k]},
                                         "dropped": [k for k in was if k not in now]}
    else:
        out["changes_since_previous"] = "first run: no previous scan to compare with"
    with open(os.path.join(a_.out, "scan.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False, default=str)
    if a_.history:
        lines = []
        if os.path.exists(a_.history):
            with open(a_.history, encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        lines = [ln for ln in lines if json.loads(ln).get("session") != str(last.date())]
        lines.append(json.dumps({"session": str(last.date()), "retrieved_istanbul": prov["retrieved_istanbul_end"],
                                 "version": cfg["version"],
                                 "shortlist": [{"id": r.get("setup_id") or r["ticker"], "state": r["state"],
                                                "pivot": r["pivot"], "close": r["close"]} for r in shortlist],
                                 "events": events}, ensure_ascii=False))
        with open(a_.history, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    for row in shortlist[:3]:
        a, f = feats[row["ticker"]]
        chart(a, f, row, os.path.join(a_.out, f"chart_{row['ticker']}.png"))
    print(json.dumps({"session": str(last.date()), "armed_or_confirmed": len(cands), "forming": len(forming),
                      "funnel": out["funnel"], "context": {k: v for k, v in context.items() if not isinstance(v, list)}}, indent=1))


if __name__ == "__main__":
    main()

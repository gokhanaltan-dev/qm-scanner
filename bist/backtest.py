"""Backtest the frozen V1 tight-base rules, a 55-session-high baseline and an XU100 benchmark.

    python -m bist.backtest --data DATA_DIR --out OUT_DIR

Writes results.json plus full ledgers (trades, skipped entries, setups, baseline trades, equity curve).
"""
from __future__ import annotations

import argparse
import copy
import json
import os

import numpy as np
import pandas as pd

from . import core


def period_of(d, cfg):
    for name, (a, b) in cfg["periods"].items():
        if pd.Timestamp(a) <= d <= pd.Timestamp(b):
            return name
    return "before"


def run_all(mk, xs, cfg, with_baseline=True, with_scores=True):
    rows_t, rows_s, rows_u, rows_b, rows_bs = [], [], [], [], []
    regime = market_regime(mk, xs)
    for tk in mk.panels["C"].columns:
        a = core.stock_arrays(mk, xs, tk)
        if a is None or len(a["C"]) < 260:
            continue
        f = core.features(a["O"], a["H"], a["L"], a["C"], a["V"], cfg)
        r = core.run_tight_base(a, f, cfg, tk)
        d = pd.DatetimeIndex(a["dates"])
        sector = mk.meta["sector"].get(tk, "Unknown")
        for s in r["setups"]:
            rows_u.append({"setup_id": s["id"], "ticker": tk, "armed": d[s["armed_i"]], "resolved": d[s["end_i"]],
                           "state": s["state"], "why": s["why"], "pivot": s["P"], "base_low": s["base_low"],
                           "sessions_armed": s["end_i"] - s["armed_i"]})
        for kind, lst in (("trade", r["trades"]), ("skip", r["skips"])):
            for t in lst:
                i = t["signal_i"]
                row = {"setup_id": t["setup_id"], "ticker": tk, "sector": sector, "signal": d[i],
                       "armed": d[t["armed_i"]], "pivot": t["P"], "atr_signal": t["atr_sig"],
                       "max_entry": t["max_entry"], "stop": t["stop"], "vol_mult": t["vol_mult"],
                       "rs_pct": a["rs_pct"][i - 1], "liq_rank": a["liq_rank"][i - 1], "med20_try": a["med20"][i - 1],
                       "adj_factor_at_signal": a["adj"][i]}
                if with_scores:
                    m = core.setup_measurements(a, f, i - 1, t["P"], t["armed_i"], cfg)
                    row["score"] = core.score_setup(m, cfg)["total"]
                row.update(regime_row(regime, d[i]))
                if kind == "trade":
                    row.update({"entry_date": d[t["entry_i"]], "entry": t["entry"], "risk": t["risk"],
                                "exit_date": d[t["exit_i"]], "exit": t["exit"], "exit_reason": t["reason"],
                                "hold": t["hold"], "open": t["open"], "uncertain_fill": t["uncertain_fill"],
                                "false_breakout": t["false_breakout"], "r_gross": t["r_gross"]})
                    rows_t.append(row)
                else:
                    row.update({"entry_date": d[t["entry_i"]], "open_price": a["O"][t["entry_i"]], "reason": t["reason"]})
                    rows_s.append(row)
        if with_baseline:
            rb = core.run_baseline(a, f, cfg, tk)
            for t in rb["trades"]:
                i = t["signal_i"]
                row = {"ticker": tk, "sector": sector, "signal": d[i], "pivot": t["P"], "stop": t["stop"],
                       "rs_pct": a["rs_pct"][i - 1], "liq_rank": a["liq_rank"][i - 1], "med20_try": a["med20"][i - 1],
                       "entry_date": d[t["entry_i"]], "entry": t["entry"], "risk": t["risk"],
                       "exit_date": d[t["exit_i"]], "exit": t["exit"], "exit_reason": t["reason"], "hold": t["hold"],
                       "open": t["open"], "uncertain_fill": t["uncertain_fill"], "false_breakout": t["false_breakout"],
                       "r_gross": t["r_gross"]}
                row.update(regime_row(regime, d[i]))
                row["score"] = row["rs_pct"] if np.isfinite(row["rs_pct"]) else 0.0
                rows_b.append(row)
            for t in rb["skips"]:
                rows_bs.append({"ticker": tk, "signal": d[t["signal_i"]], "reason": t["reason"]})
    T = pd.DataFrame(rows_t)
    for df in (T, pd.DataFrame(rows_b)):
        pass
    return {"trades": T, "skips": pd.DataFrame(rows_s), "setups": pd.DataFrame(rows_u),
            "base_trades": pd.DataFrame(rows_b), "base_skips": pd.DataFrame(rows_bs), "regime": regime}


def market_regime(mk, xs):
    x = mk.xu100
    s50, s200 = x.rolling(50).mean(), x.rolling(200).mean()
    C = mk.panels["C"]
    above = (C > C.rolling(50, min_periods=50).mean()).where(xs["member"])
    breadth = above.sum(axis=1) / xs["member"].sum(axis=1).replace(0, np.nan)
    return pd.DataFrame({"xu100": x, "xu_sma50": s50, "xu_sma200": s200,
                         "xu_uptrend": (x > s50) & (s50 > s200), "xu_above_50": x > s50, "breadth50": breadth})


def regime_row(regime, d):
    r = regime.loc[d]
    return {"xu_uptrend": bool(r["xu_uptrend"]), "xu_above_50": bool(r["xu_above_50"]), "breadth50": float(r["breadth50"])}


def add_net(T, cfg):
    if T.empty:
        return T
    T = T.copy()
    for bps in cfg["costs_bps_per_side"]:
        c = bps / 1e4
        T[f"r_net_{bps}"] = (T["exit"] * (1 - c) - T["entry"] * (1 + c)) / T["risk"]
        T[f"ret_net_{bps}"] = T["exit"] * (1 - c) / (T["entry"] * (1 + c)) - 1
    T["period"] = [period_of(d, cfg) for d in T["signal"]]
    T["exit_period"] = [period_of(d, cfg) for d in T["exit_date"]]
    T["crosses_boundary"] = T["period"] != T["exit_period"]
    T["liq_bucket"] = pd.cut(T["liq_rank"], [0, 50, 100, 200], labels=["1-50", "51-100", "101-200"]).astype(str)
    T["year"] = T["signal"].dt.year
    return T


def stats(T, col, rng=None):
    T = T[~T["open"]] if "open" in T else T
    n = len(T)
    if n == 0:
        return {"trades": 0}
    r = T[col].values
    wins, losses = r[r > 0], r[r <= 0]
    out = {"trades": int(n), "win_rate": round(float((r > 0).mean()), 3),
           "avg_win_R": round(float(wins.mean()), 2) if len(wins) else None,
           "avg_loss_R": round(float(losses.mean()), 2) if len(losses) else None,
           "expectancy_R": round(float(r.mean()), 3), "median_R": round(float(np.median(r)), 3),
           "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) and losses.sum() < 0 else None,
           "avg_hold_sessions": round(float(T["hold"].mean()), 1),
           "false_breakout_rate": round(float(T["false_breakout"].mean()), 3)}
    if rng is not None and n >= 20:
        # Signals cluster in time, so resample whole signal-months rather than single trades.
        key = T["signal"].dt.to_period("M").astype(str).values
        groups = [r[key == k] for k in np.unique(key)]
        means = []
        for _ in range(2000):
            pick = rng.integers(0, len(groups), len(groups))
            x = np.concatenate([groups[j] for j in pick])
            means.append(x.mean())
        out["expectancy_R_ci90"] = [round(float(np.percentile(means, 5)), 3), round(float(np.percentile(means, 95)), 3)]
        out["signal_months"] = len(groups)
    return out


def portfolio(T, mk, cfg, bps, rank_col="score"):
    """Daily cash-and-positions simulation over the trade ledger. Entries fill before that day's exits,
    so a slot freed today is only reusable tomorrow."""
    p = cfg["portfolio"]
    c = bps / 1e4
    dates = mk.dates
    Cf = mk.panels["C"].ffill()
    T = T[~T["open"]].sort_values(["entry_date", rank_col], ascending=[True, False]).reset_index(drop=True)
    by_entry = {d: g for d, g in T.groupby("entry_date")}
    cash, equity_prev = float(p["start_equity_try"]), float(p["start_equity_try"])
    pos = []
    eq, expo, taken, skipped, buys = [], [], [], [], 0.0
    first = T["entry_date"].min() if len(T) else dates[0]
    for d in dates:
        if d in by_entry:
            for _, t in by_entry[d].iterrows():
                why = None
                open_risk = sum(x["risk_try"] for x in pos)
                if any(x["ticker"] == t["ticker"] for x in pos):
                    why = "already holding this share"
                elif len(pos) >= p["max_positions"]:
                    why = "four positions already open"
                elif sum(1 for x in pos if x["sector"] == t["sector"]) >= p["max_per_sector"]:
                    why = "two positions already in this sector"
                else:
                    risk_budget = min(p["risk_per_trade"] * equity_prev, p["max_open_risk"] * equity_prev - open_risk)
                    if risk_budget <= 0.2 * p["risk_per_trade"] * equity_prev:
                        why = "aggregate open risk limit"
                    else:
                        notional = risk_budget / (t["risk"] / t["entry"])
                        cap_liq = p["max_pct_of_median_turnover"] * (t["med20_try"] if np.isfinite(t["med20_try"]) else 0)
                        notional = min(notional, cap_liq, cash / (1 + c))
                        if notional < 0.2 * risk_budget / (t["risk"] / t["entry"]):
                            why = "liquidity cap or cash too small for a meaningful position"
                if why:
                    skipped.append({"setup_id": t.get("setup_id"), "ticker": t["ticker"], "entry_date": d, "reason": why})
                    continue
                sh = notional / t["entry"]
                cash -= notional * (1 + c)
                buys += notional
                pos.append({"ticker": t["ticker"], "sector": t["sector"], "shares": sh, "exit_date": t["exit_date"],
                            "exit": t["exit"], "risk_try": sh * t["risk"], "setup_id": t.get("setup_id")})
                taken.append({"setup_id": t.get("setup_id"), "ticker": t["ticker"], "entry_date": d, "notional": notional,
                              "equity_before": equity_prev, "r_net": t[f"r_net_{bps}"], "signal": t["signal"]})
        keep = []
        for x in pos:
            if x["exit_date"] == d:
                cash += x["shares"] * x["exit"] * (1 - c)
            else:
                keep.append(x)
        pos = keep
        mv = sum(x["shares"] * Cf.at[d, x["ticker"]] for x in pos)
        equity_prev = cash + mv
        eq.append(equity_prev)
        expo.append(mv / equity_prev if equity_prev > 0 else 0)
    E = pd.Series(eq, index=dates)
    return {"equity": E, "exposure": pd.Series(expo, index=dates), "taken": pd.DataFrame(taken),
            "skipped": pd.DataFrame(skipped), "buys": buys}


def curve_stats(E, a, b):
    s = E.loc[a:b]
    if len(s) < 30:
        return None
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    tot = s.iloc[-1] / s.iloc[0]
    dd = (s / s.cummax() - 1).min()
    return {"from": str(s.index[0].date()), "to": str(s.index[-1].date()), "total_return": round(float(tot - 1), 4),
            "cagr": round(float(tot ** (1 / yrs) - 1), 4), "max_drawdown": round(float(dd), 4)}


def summarise(T, cfg, rng, label):
    bps = cfg["headline_cost_bps"]
    col = f"r_net_{bps}"
    closed = T[~T["open"]]
    out = {"label": label, "open_trades_excluded": int(T["open"].sum()),
           "trades_crossing_period_boundary_purged": int(closed["crosses_boundary"].sum())}
    clean = closed[~closed["crosses_boundary"]]
    out["by_period"] = {}
    for per in ["development", "validation", "final_test"]:
        g = clean[clean["period"] == per]
        d = {"net_25bps": stats(g, col, rng), "gross": stats(g, "r_gross"),
             "cost_sensitivity_expectancy_R": {str(b): (round(float(g[f"r_net_{b}"].mean()), 3) if len(g) else None)
                                               for b in cfg["costs_bps_per_side"]},
             "mean_trade_return_net": round(float(g[f"ret_net_{bps}"].mean()), 4) if len(g) else None,
             "uncertain_fills": int(g["uncertain_fill"].sum()) if len(g) else 0}
        out["by_period"][per] = d
    out["all_periods_net_25bps"] = stats(clean, col, rng)
    out["by_year_net_25bps"] = {int(y): stats(g, col) for y, g in clean.groupby("year")}
    out["by_regime_net_25bps"] = {"xu100_uptrend": stats(clean[clean["xu_uptrend"]], col),
                                  "xu100_not_uptrend": stats(clean[~clean["xu_uptrend"]], col),
                                  "breadth_above_50pct": stats(clean[clean["breadth50"] >= 0.5], col),
                                  "breadth_below_50pct": stats(clean[clean["breadth50"] < 0.5], col)}
    out["by_liquidity_net_25bps"] = {k: stats(g, col) for k, g in clean.groupby("liq_bucket")}
    out["exit_reasons"] = clean["exit_reason"].value_counts().to_dict()
    if "score" in clean and "setup_id" in clean and len(clean) >= 30:
        q = clean["score"].quantile([1 / 3, 2 / 3]).values
        lab = np.where(clean["score"] <= q[0], "low", np.where(clean["score"] <= q[1], "mid", "high"))
        out["by_score_tercile_net_25bps"] = {k: stats(clean[lab == k], col) for k in ["low", "mid", "high"]}
        out["score_tercile_cutoffs"] = [round(float(x), 1) for x in q]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--no-sensitivity", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = core.load_config()
    rng = np.random.default_rng(20261110)
    mk = core.build_market(a.data, cfg)
    xs = core.cross_section(mk, cfg)
    res = run_all(mk, xs, cfg)
    T, B = add_net(res["trades"], cfg), add_net(res["base_trades"], cfg)
    bps = cfg["headline_cost_bps"]

    R = {"version": cfg["version"], "data": {k: v for k, v in mk.quality.items() if k != "provenance"},
         "provenance": mk.quality["provenance"], "periods": cfg["periods"],
         "universe_note": ("Universe is rebuilt each session from trailing liquidity, but only shares still listed "
                           "on the retrieval date are in the data: delisted and merged shares are missing "
                           "(survivorship bias, which flatters results)."),
         "setups": res["setups"]["state"].value_counts().to_dict() if len(res["setups"]) else {},
         "signals": int(len(T) + len(res["skips"])),
         "skipped_entries": res["skips"]["reason"].value_counts().to_dict() if len(res["skips"]) else {}}
    R["tight_base"] = summarise(T, cfg, rng, "tight-base breakout V1") if len(T) else {"trades": 0}
    R["baseline_55"] = summarise(B, cfg, rng, "55-session-high breakout") if len(B) else {"trades": 0}
    R["baseline_55"]["skipped_entries"] = res["base_skips"]["reason"].value_counts().to_dict() if len(res["base_skips"]) else {}

    # Portfolio simulations and benchmark
    first = pd.Timestamp(cfg["periods"]["development"][0])
    R["portfolio"] = {}
    for name, df, rank in (("tight_base", T, "score"), ("baseline_55", B, "score")):
        if df.empty:
            continue
        pf = portfolio(df[~df["crosses_boundary"]] if False else df, mk, cfg, bps, rank)
        E = pf["equity"].loc[first:]
        d = {"cost_bps_per_side": bps, "trades_taken": int(len(pf["taken"])),
             "skipped_for_portfolio_limits": pf["skipped"]["reason"].value_counts().to_dict() if len(pf["skipped"]) else {},
             "avg_exposure": round(float(pf["exposure"].loc[first:].mean()), 3),
             "turnover_per_year": round(float(pf["buys"] / E.mean() / ((E.index[-1] - E.index[0]).days / 365.25)), 2),
             "periods": {k: curve_stats(E, *v) for k, v in cfg["periods"].items()},
             "whole": curve_stats(E, E.index[0], E.index[-1])}
        usd = (E / mk.usdtry.reindex(E.index)).dropna()
        d["whole_usd"] = curve_stats(usd, usd.index[0], usd.index[-1])
        R["portfolio"][name] = d
        pf["equity"].loc[first:].rename("equity_try").to_csv(os.path.join(a.out, f"equity_{name}.csv"))
        pf["skipped"].to_csv(os.path.join(a.out, f"portfolio_skipped_{name}.csv"), index=False)
        pf["taken"].to_csv(os.path.join(a.out, f"portfolio_taken_{name}.csv"), index=False)
    X = mk.xu100.loc[first:]
    XU = (mk.xu100 / mk.usdtry).loc[first:].dropna()
    R["benchmark_xu100"] = {"note": "XU100 price index, buy and hold, no costs, dividends excluded; share prices in "
                                    "the strategy are dividend-adjusted, so the strategy has a small built-in advantage.",
                            "periods": {k: curve_stats(X, *v) for k, v in cfg["periods"].items()},
                            "whole": curve_stats(X, X.index[0], X.index[-1]),
                            "whole_usd": curve_stats(XU, XU.index[0], XU.index[-1])}

    # Limited one-at-a-time sensitivity, development period only. Every variant tried is listed.
    if not a.no_sensitivity:
        variants = [("relative_strength.min_percentile", 70), ("relative_strength.min_percentile", 90),
                    ("confirmation.volume_multiple", 1.2), ("confirmation.volume_multiple", 2.0),
                    ("base.max_depth", 0.15), ("base.max_depth", 0.25),
                    ("base.max_volume_ratio", 1.0), ("base.max_contraction_ratio", 0.8)]
        sens = []
        for key, val in variants:
            c2 = copy.deepcopy(cfg)
            k1, k2 = key.split(".")
            c2[k1][k2] = val
            r2 = run_all(mk, xs, c2, with_baseline=False, with_scores=False)
            T2 = add_net(r2["trades"], c2)
            g = T2[(~T2["open"]) & (~T2["crosses_boundary"]) & (T2["period"] == "development")] if len(T2) else T2
            sens.append({"parameter": key, "value": val, "development": stats(g, f"r_net_{bps}") if len(g) else {"trades": 0}})
        R["sensitivity_development_only"] = sens

    for name, df in (("trades", T), ("skipped_entries", res["skips"]), ("setups", res["setups"]),
                     ("baseline_trades", B), ("regime", res["regime"].loc[first:])):
        df.to_csv(os.path.join(a.out, f"{name}.csv"), index=(name == "regime"))
    with open(os.path.join(a.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str, ensure_ascii=False)
    print(json.dumps({k: R[k] for k in ("version", "data", "setups", "signals", "skipped_entries")}, indent=1, default=str))


if __name__ == "__main__":
    main()

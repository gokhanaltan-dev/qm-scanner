"""Summarise a backtest: what paid, by rating, by market light and by factor.

    python -m backtest.analyse bt_out            # prints tables
    python -m backtest.analyse bt_out --json backtest/summary.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

R_CAP = 12.0      # one freak winner should not decide a comparison


def load(folder: str | Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    folder = Path(folder)
    rows = pd.read_csv(folder / "rows.csv.gz", low_memory=False)
    daily = pd.read_csv(folder / "daily.csv")
    meta = json.loads((folder / "meta.json").read_text()) if (folder / "meta.json").exists() else {}
    rows = rows.merge(daily, on="date", how="left").copy()
    for col in ("traded", "trig_next", "complete", "ambiguous", "hit2r", "took_half", "wide"):
        if col in rows:
            rows[col] = rows[col].fillna(False).astype(bool)
    rows["rc"] = rows["r"].clip(upper=R_CAP)
    rows["light"] = light(rows)
    return rows, daily, meta


def light(df: pd.DataFrame) -> pd.Series:
    """The sheet's light: the Nasdaq state, with green cut to amber on weak breadth
    or failing breakouts."""
    s = df["qqq_state"].copy()
    weak = (df["above20"] < 40) | (df["ft"] < 40)
    s[(s == "green") & weak] = "amber"
    return s


def stats(t: pd.DataFrame) -> dict:
    """Trade statistics in R. Only finished trades count."""
    t = t[t["traded"] & t["complete"]]
    n = len(t)
    if n == 0:
        return {"n": 0}
    wins, losses = t[t["r"] > 0]["r"], t[t["r"] <= 0]["r"]
    se = t["rc"].std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    return {
        "n": n, "win": round(len(wins) / n * 100, 1), "avg_r": round(t["rc"].mean(), 3),
        "se": round(se, 3), "median_r": round(t["r"].median(), 3),
        "avg_win": round(wins.clip(upper=R_CAP).mean(), 2) if len(wins) else None,
        "avg_loss": round(losses.mean(), 2) if len(losses) else None,
        "hit2r": round(t["hit2r"].mean() * 100, 1),
        "avg_r_pess": round(t["r_pess"].clip(upper=R_CAP).mean(), 3),
        "pf": round(wins.clip(upper=R_CAP).sum() / -losses.sum(), 2) if losses.sum() < 0 else None,
        "hold": round(t["hold"].mean(), 1),
    }


def table(t: pd.DataFrame, by, order=None) -> pd.DataFrame:
    out = {}
    for key, g in t.groupby(by, observed=True):
        out[key] = stats(g)
    df = pd.DataFrame(out).T
    if order:
        df = df.reindex([k for k in order if k in df.index])
    return df


def bands(t: pd.DataFrame, col: str, q: int = 5) -> pd.DataFrame:
    """Trades split into equal-sized groups by one factor, lowest values first."""
    t = t[t["traded"] & t["complete"] & t[col].notna()].copy()
    if t[col].nunique() <= 2:
        t["band"] = t[col].astype(float)
    else:
        try:
            t["band"] = pd.qcut(t[col].astype(float), q, duplicates="drop")
        except ValueError:
            return pd.DataFrame()
    return table(t, "band")


def rank_corr(t: pd.DataFrame, col: str, target: str = "rc") -> float:
    t = t[t[col].notna() & t[target].notna()]
    if len(t) < 30 or t[col].nunique() < 2:
        return np.nan
    return float(t[col].astype(float).rank().corr(t[target].rank()))


def factor_report(trades: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Rank correlation of each factor with the trade result, on the whole sample and
    on each half of the dates. A factor worth using has the same sign in both halves."""
    trades = trades[trades["traded"] & trades["complete"]]
    cut = trades["date"].sort_values().iloc[len(trades) // 2]
    first, second = trades[trades["date"] <= cut], trades[trades["date"] > cut]
    out = []
    for c in cols:
        if c not in trades:
            continue
        b = bands(trades, c)
        out.append({"factor": c, "corr": rank_corr(trades, c), "first_half": rank_corr(first, c),
                    "second_half": rank_corr(second, c),
                    "low_band_r": b["avg_r"].iloc[0] if len(b) else np.nan,
                    "high_band_r": b["avg_r"].iloc[-1] if len(b) else np.nan,
                    "n": int(trades[c].notna().sum())})
    return pd.DataFrame(out).set_index("factor").round(3)


BASE_FACTORS = ["score", "s_leader", "s_tight", "s_volume", "s_ma", "s_base", "s_trigger", "s_tradeable",
                "rs", "rs1", "rs3", "rs6", "b_leg_gain", "b_leg_days", "b_base_days", "b_depth",
                "b_retrace", "b_tight", "b_close_span", "b_higher_lows", "b_vol_ratio", "b_ma_stack",
                "b_ma_dist", "b_dist", "adr", "dollar_vol", "price", "gap_open", "risk_pct",
                "pull_rs", "rsline", "ext50", "ext200", "streak50", "ud_vol", "off_high", "p6m",
                "base_count", "ind_leaders", "sec_leaders", "ind_share", "ind_rank",
                "above20", "above50", "ft", "qqq_vs10", "qqq_vs20", "qqq_vs50", "n_leaders"]
EP_FACTORS = ["score", "s_gap", "s_volume", "s_close", "s_fresh", "s_tradeable", "e_age", "e_gap",
              "e_vol_mult", "e_close_pos", "e_prior_3m", "e_clears_6m_high", "e_adr_before", "e_dist",
              "adr", "dollar_vol", "price", "rs", "ind_rank", "ind_leaders", "pull_rs", "above20", "ft"]


EXIT_RULES = [("r", "Sell half from day 3, rest on a close under the 10-day (the sheet's rules)"),
              ("r_ma10", "No partial, sell on a close under the 10-day"),
              ("r_ma20", "No partial, sell on a close under the 20-day"),
              ("r_h20", "Sell half from day 3, rest on a close under the 20-day"),
              ("r_p2", "Sell half at +2R, rest on a close under the 10-day"),
              ("r_t2", "Sell everything at +2R"),
              ("r_t3", "Sell everything at +3R"),
              ("r_tight", "The sheet's rules with the stop half as far")]

# Thin, wild stocks: the gaps that collapse. Same thresholds as config.yaml.
EP_GATE = {"min_pre_price": 5.0, "max_prior_adr": 0.08, "max_gap": 0.5, "min_pre_dollar_volume": 5e6,
           "max_adr_from_high": 0.5}


def ep_junk(ep: pd.DataFrame) -> pd.Series:
    g = EP_GATE
    return ((ep["e_pre_close"] < g["min_pre_price"]) | (ep["e_adr_before"] >= g["max_prior_adr"])
            | (ep["e_gap"] >= g["max_gap"]) | (ep["e_pre_dollar_vol"] < g["min_pre_dollar_volume"]))


def brief(t: pd.DataFrame) -> dict:
    s = stats(t)
    return {k: (None if isinstance(v, float) and v != v else v)
            for k, v in s.items() if k in ("n", "win", "avg_r", "se", "hit2r", "avg_r_pess")}


def summary(rows: pd.DataFrame, daily: pd.DataFrame, meta: dict) -> dict:
    """The numbers shown on the sheet and in the README."""
    q = (1 + daily["qqq_chg"]).cumprod()
    x10 = rows["fwd10"] - rows["date"].map(dict(zip(daily["date"], q.shift(-10) / q - 1)))
    rows = rows.assign(x10=x10)
    cut = daily["date"].iloc[len(daily) // 2]
    base, ep = rows[rows["kind"] == "base"], rows[rows["kind"] == "ep"].copy()
    ready = base[base["status"] == "ready"]
    lights = ["green", "amber", "red"]
    out = {
        "generated": meta.get("generated"), "first": meta.get("first"), "last": meta.get("last"),
        "sessions": meta.get("sessions"), "stocks": meta.get("stocks"),
        "flags": int(len(base)), "ready_flags": int(len(ready)),
        "ready_per_day": round(len(ready) / len(daily), 1),
        "trigger_next_day": round(float(ready["trig_next"].mean() * 100), 1),
        "trigger_in_5": round(float(ready["trig_days"].notna().mean() * 100), 1),
        "light_days": {k: int(v) for k, v in light(daily).value_counts().items()},
        "all_triggers": brief(base), "ready": brief(ready),
        "ready_by_light": {k: brief(ready[ready["light"] == k]) for k in lights},
        "ready_by_light_halves": {k: [brief(ready[(ready["light"] == k) & (ready["date"] <= cut)]),
                                      brief(ready[(ready["light"] == k) & (ready["date"] > cut)])]
                                  for k in lights},
        "ready_by_year": {y: brief(g) for y, g in ready.groupby(ready["date"].str[:4])},
        "ready_by_rating": [{"band": name, **brief(ready[(ready["score"] >= lo) & (ready["score"] < hi)])}
                            for name, lo, hi in (("Under 62 (C)", 0, 62), ("62 to 74 (B)", 62, 75),
                                                 ("75 to 84 (A)", 75, 85), ("85 and over (A+)", 85, 101))],
        "risk_pct_median": round(float(base[base["traded"]]["risk_pct"].median() * 100), 1),
        "ambiguous_pct": round(float(base[base["traded"]]["ambiguous"].mean() * 100), 1),
        "half_split": cut,
    }
    done = ready[ready["traded"] & ready["complete"]]
    green = done[done["light"] == "green"]
    out["exit_rules"] = [{"rule": label, "ready": round(float(done[col].clip(upper=R_CAP).mean()), 3),
                          "ready_green": round(float(green[col].clip(upper=R_CAP).mean()), 3),
                          "win_green": round(float((green[col] > 0).mean() * 100), 1)}
                         for col, label in EXIT_RULES if col in done]
    # factors: rank correlation with the result in each half of the period
    fr = factor_report(base, [f for f in BASE_FACTORS if f in base])
    out["factors"] = {k: [None if v != v else round(float(v), 3) for v in (r["first_half"], r["second_half"])]
                      for k, r in fr.iterrows()}
    # episodic pivots
    ep["junk"] = ep_junk(ep)
    today = ep[ep["status"] == "today"]
    near = ~ep["junk"] & (ep["e_dist"] <= EP_GATE["max_adr_from_high"])

    def gap_day(g):
        g = g[g["x10"].notna()]
        return {"n": int(len(g)), "vs_market_10d": round(float(g["x10"].mean() * 100), 1),
                "median": round(float(g["x10"].median() * 100), 1)}
    out["ep"] = {
        "gate": EP_GATE,
        "thin_share": round(float(ep["junk"].mean() * 100)),
        "gap_day_all": gap_day(today), "gap_day_thin": gap_day(today[today["junk"]]),
        "gap_day_clean": gap_day(today[~today["junk"]]),
        "trades_thin": brief(ep[ep["junk"]]), "trades_clean": brief(ep[~ep["junk"]]),
        "trades_clean_near_high": brief(ep[near]),
        "trades_clean_far": brief(ep[~ep["junk"] & ~near]),
        "by_distance": [{"band": name, **brief(ep[~ep["junk"] & (ep["e_dist"] > lo) & (ep["e_dist"] <= hi)])}
                        for name, lo, hi in (("Within 0.25", -9, 0.25), ("0.25 to 0.5", 0.25, 0.5),
                                             ("0.5 to 1", 0.5, 1.0), ("1 to 1.5", 1.0, 9))],
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--json", help="write the summary used by the sheet to this file")
    args = ap.parse_args()
    pd.set_option("display.width", 220, "display.max_columns", 30, "display.max_rows", 200)
    rows, daily, meta = load(args.folder)
    if args.json:
        Path(args.json).write_text(json.dumps(summary(rows, daily, meta), indent=1))
        print(f"summary written to {args.json}")
        return
    print(meta)
    print(rows.groupby(["kind", "status"]).size())
    base = rows[rows["kind"] == "base"]
    print("\n== base setups that triggered the next session ==")
    print(pd.DataFrame({"all": stats(base), "ready": stats(base[base.status == "ready"]),
                        "building": stats(base[base.status == "building"])}).T)
    print("\nby light");  print(table(base, "light", ["green", "amber", "red"]))
    print("\nby light, ready only"); print(table(base[base.status == "ready"], "light", ["green", "amber", "red"]))
    print("\nby rating band")
    b = base.assign(band=pd.cut(base["score"], [0, 55, 62, 75, 85, 101], right=False))
    print(table(b, "band"))
    print("\nfactors"); print(factor_report(base, BASE_FACTORS))


if __name__ == "__main__":
    main()

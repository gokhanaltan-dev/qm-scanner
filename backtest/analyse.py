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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    args = ap.parse_args()
    pd.set_option("display.width", 220, "display.max_columns", 30, "display.max_rows", 200)
    rows, daily, meta = load(args.folder)
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

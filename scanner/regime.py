"""Market regime: should breakouts be traded at all today?

The core read is Qullamaggie's: the Nasdaq's 10-day and 20-day moving averages.
Rising and stacked 10 over 20 means breakouts tend to work. 10 under 20 means they
tend to fail. Breadth and breakout follow-through can downgrade a green light.
"""
from __future__ import annotations

import pandas as pd


def index_state(close: pd.Series) -> dict:
    s = close.dropna()
    s10, s20, s50 = (s.rolling(n).mean() for n in (10, 20, 50))
    c = s.iloc[-1]
    up10, up20 = s10.iloc[-1] > s10.iloc[-4], s20.iloc[-1] > s20.iloc[-6]
    if c > s20.iloc[-1] and s10.iloc[-1] > s20.iloc[-1] and up10 and up20:
        state = "green"
    elif s10.iloc[-1] < s20.iloc[-1] and c < s20.iloc[-1]:
        state = "red"
    else:
        state = "amber"
    return {
        "state": state, "close": float(c),
        "chg": float(c / s.iloc[-2] - 1), "w1": float(c / s.iloc[-6] - 1),
        "vs10": float(c / s10.iloc[-1] - 1), "vs20": float(c / s20.iloc[-1] - 1),
        "vs50": float(c / s50.iloc[-1] - 1),
        "ma10_over_20": bool(s10.iloc[-1] > s20.iloc[-1]),
        "ma10_rising": bool(up10), "ma20_rising": bool(up20),
    }


def describe(ix: dict) -> str:
    order = "10-day above 20-day" if ix["ma10_over_20"] else "10-day below 20-day"
    slope = ("both rising" if ix["ma10_rising"] and ix["ma20_rising"]
             else "both falling" if not ix["ma10_rising"] and not ix["ma20_rising"]
             else "10-day rising, 20-day falling" if ix["ma10_rising"]
             else "10-day turning down")
    where = "price above both" if ix["vs10"] > 0 and ix["vs20"] > 0 else (
        "price below both" if ix["vs10"] < 0 and ix["vs20"] < 0 else "price between them")
    return f"{order}, {slope}, {where}"


def assess(index_close: dict[str, pd.Series], br: dict) -> dict:
    ix = {t: index_state(s) for t, s in index_close.items()}
    state = ix["QQQ"]["state"]
    reasons = [f"Nasdaq 100: {describe(ix['QQQ'])}."]
    downgraded = False

    ft = br.get("followthrough")
    if state == "green":
        if br["above20"] < 40:
            downgraded = True
            reasons.append(f"Only {br['above20']:.0f}% of stocks are above their 20-day average: "
                           "the index is being carried by a few names.")
        if ft is not None and ft < 40:
            downgraded = True
            reasons.append(f"Only {ft:.0f}% of the last two weeks' breakouts are still above "
                           "their breakout close.")
        if downgraded:
            state = "amber"
    if state == "amber" and not downgraded and ix["QQQ"]["state"] == "amber":
        reasons.append("The trend is not clean in either direction.")
    if ft is not None and not (downgraded and ft < 40):
        reasons.append(f"{ft:.0f}% of the last two weeks' breakouts are holding above their "
                       "breakout close.")

    verdict = {
        "green": ("Green light. Trade breakouts at full size.", 1.0),
        "amber": ("Amber. Half size, best setups only.", 0.5),
        "red": ("Red light. No new breakouts.", 0.0),
    }[state]
    action = {
        "green": "Take ready setups as they trigger. Sell a third to a half into strength "
                 "after three to five days and trail the rest on the 10-day average.",
        "amber": "Only take A-rated setups, at half risk. Take partial profits sooner.",
        "red": "Breakouts tend to fail when the Nasdaq's 10-day is under its 20-day. Keep the "
               "list as a watchlist of leaders for when the light changes. A strong episodic "
               "pivot is the only long worth a small position.",
    }[state]
    return {"state": state, "verdict": verdict[0], "size_factor": verdict[1],
            "action": action, "reasons": reasons, "indexes": ix}

"""The check after the US open: which of last night's triggers have fired?

    python -m scanner.intraday

Runs in the first two hours of the session. For every stock on the ready and episodic
pivot lists it reads today's 5-minute bars and reports whether price has traded
through the trigger, where it is now, the low of the day (the real stop), the size
that stop allows, and how much of a normal day's volume has traded so far.

The result is saved to data/open_check.json and shown at the top of the page until
the next evening scan replaces the sheet.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from . import config as C
from . import setups

NY = ZoneInfo("America/New_York")
# Only run between 09:50 and 11:30 New York time. The window is wide because GitHub
# often starts scheduled jobs 30 to 45 minutes late.
WINDOW = (9 * 60 + 50, 11 * 60 + 30)


def _log(msg: str) -> None:
    print(f"[open-check] {msg}", flush=True)


def due(now: datetime, asof: str) -> str | None:
    """None if the check should run now, otherwise the reason it should not."""
    ny = now.astimezone(NY)
    if ny.weekday() >= 5:
        return "weekend"
    minutes = ny.hour * 60 + ny.minute
    if not WINDOW[0] <= minutes <= WINDOW[1]:
        return f"outside the window ({ny:%H:%M} New York)"
    age = (ny.date() - datetime.strptime(asof, "%Y-%m-%d").date()).days
    if age <= 0:
        return "the sheet is already for today"
    if age > 5:
        return f"the sheet is {age} days old"
    return None


def fetch_5m(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Today's regular-session 5-minute bars per ticker, in New York time."""
    import yfinance as yf

    df = yf.download(tickers, period="1d", interval="5m", auto_adjust=False, progress=False,
                     threads=True, group_by="ticker", prepost=False, timeout=30,
                     multi_level_index=True)
    out = {}
    if df is None or df.empty:
        return out
    for t in tickers:
        try:
            one = df[t].dropna(subset=["Close"])
        except KeyError:
            continue
        if one.empty:
            continue
        idx = one.index
        one = one.copy()
        one.index = idx.tz_convert(NY) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(NY)
        out[t] = one
    return out


def assess(row: dict, bars: pd.DataFrame, cfg: dict, size_factor: float) -> dict:
    """One stock: has it traded through the trigger, and is it still buyable?"""
    level = row["pivot"] * 1.001
    adr = row["adr_pct"] / 100
    vol50 = [x for x in row["_chart"]["volume"][-50:] if x is not None and x == x]
    avg_vol = sum(vol50) / len(vol50) if vol50 else float("nan")
    now = float(bars["Close"].iloc[-1])
    lod, hod = float(bars["Low"].min()), float(bars["High"].max())
    vol_pct = float(bars["Volume"].sum()) / avg_vol * 100 if avg_vol and avg_vol == avg_vol else None
    out = {"ticker": row["ticker"], "setup": row["setup"], "score": row["score"],
           "grade": row["grade"], "trigger": round(row["pivot"], 2), "now": round(now, 2),
           "low": round(lod, 2), "high": round(hod, 2),
           "vol_pct": None if vol_pct is None else round(vol_pct),
           "vs_trigger": now / row["pivot"] - 1}
    crossed = bars[bars["High"] >= level]
    if crossed.empty:
        out["state"] = "waiting"
        return out
    first = crossed.index[0]
    entry = max(level, float(crossed["Open"].iloc[0]))
    stop = float(bars.loc[:first, "Low"].min())            # low of the day when it triggered
    plan = setups.plan(entry, stop, adr, cfg, size_factor, fixed_stop=True)
    out.update(time=first.strftime("%H:%M"), entry=round(entry, 2), stop=round(stop, 2),
               risk_pct=plan["risk_pct"], stop_adr=plan["stop_adr"], shares=plan["shares"],
               position_pct=plan["position_pct"], risk_usd=plan["risk_usd"],
               too_wide=plan["stop_too_wide"], extended=(now / entry - 1) / adr if adr else 0.0)
    if now <= stop:
        out["state"] = "failed"
    elif now < row["pivot"]:
        out["state"] = "back_under"
    else:
        out["state"] = "triggered"
    return out


def run(now: datetime | None = None, force: bool = False) -> dict | None:
    now = now or datetime.now(tz=NY)
    path = C.DATA / "latest.json"
    if not path.exists():
        _log("no saved scan yet")
        return None
    payload = json.loads(path.read_text())
    why = None if force else due(now, payload["asof"])
    if why:
        _log(f"skipped: {why}")
        return None
    watch = [r for k in ("ready", "episodic") for r in payload[k]]
    if not watch:
        _log("skipped: nothing on the ready or episodic pivot lists")
        return None
    cfg = C.load()
    bars = fetch_5m([r["ticker"] for r in watch])
    today = now.astimezone(NY).date()
    bars = {t: b[b.index.date == today] for t, b in bars.items()}
    bars = {t: b for t, b in bars.items() if len(b)}
    if not bars:
        _log("skipped: no bars for today (holiday, or the data is not in yet)")
        return None
    last_bar = max(b.index[-1] for b in bars.values())
    sf = payload["regime"]["size_factor"]
    rows = []
    for r in watch:
        if r["ticker"] not in bars:
            continue
        factor = max(sf, 0.5) if r["setup"] == "ep" else sf
        rows.append(assess(r, bars[r["ticker"]], cfg, factor))
    order = {"triggered": 0, "back_under": 1, "failed": 2, "waiting": 3}
    rows.sort(key=lambda x: (order[x["state"]], -x["score"]))
    result = {"for_asof": payload["asof"], "date": today.isoformat(),
              "time_ny": (last_bar + timedelta(minutes=5)).strftime("%H:%M"),
              "minutes_in": int((last_bar + timedelta(minutes=5)
                                 - last_bar.replace(hour=9, minute=30)).total_seconds() // 60),
              "regime": payload["regime"]["state"], "rows": rows,
              "missing": [r["ticker"] for r in watch if r["ticker"] not in bars]}
    # round-trip through JSON so NaN becomes null and numpy numbers become plain ones
    clean = json.loads(json.dumps(result, default=float), parse_constant=lambda _: None)
    (C.DATA / "open_check.json").write_text(json.dumps(clean, indent=1))
    n = sum(1 for x in rows if x["state"] == "triggered")
    _log(f"{clean['time_ny']} New York: {n} triggered of {len(rows)} watched")
    return clean


if __name__ == "__main__":
    done = run(force="--force" in sys.argv)
    if done:
        from . import build
        build.rerender()
    # tell the workflow whether there is anything new to publish
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"updated={'true' if done else 'false'}\n")

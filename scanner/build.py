"""Run the daily scan and write the report.

    python -m scanner.build          # live data
    python -m scanner.build --demo   # invented data, for a preview without network
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config as C
from . import regime, screen, setups


def _log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def _arrays(bars: dict[str, pd.DataFrame], t: str):
    return tuple(bars[f][t].to_numpy(dtype=float) for f in ("Open", "High", "Low", "Close", "Volume"))


def _chart(bars: dict[str, pd.DataFrame], t: str, n: int = 70) -> dict:
    c = bars["Close"][t]
    out = {f.lower(): bars[f][t].iloc[-n:].round(4).tolist() for f in ("Open", "High", "Low", "Close", "Volume")}
    out["sma10"] = c.rolling(10).mean().iloc[-n:].round(4).tolist()
    out["sma20"] = c.rolling(20).mean().iloc[-n:].round(4).tolist()
    return out


def _pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%"


def _why_base(b: dict) -> list[str]:
    ma = ("Riding its rising 10-day and 20-day averages." if b["ma_stack"]
          else "Under its 10-day average but holding a rising 20-day." if b["ma_ok"]
          else "Below its 20-day average: the base is not being supported yet.")
    lows = ("higher lows" if b["higher_lows"] else "lows holding level" if b["holding"]
            else "lower lows")
    return [
        f"Ran {_pct(b['leg_gain'])} in {b['leg_days']} sessions, then a {b['base_days']}-day "
        f"base that gave back {_pct(b['retrace'])} of the move, with {lows}.",
        f"Daily range is {_pct(b['tight'])} of what it was during the run, and volume is "
        f"{_pct(b['vol_ratio'])} of its 50-day average.",
        ma,
    ]


def _base_call(b: dict, status: str) -> str:
    if status == "ready":
        return (f"Trigger is {_pct(b['pivot'] / b['close'] - 1, 1)} away "
                f"({b['dist']:.1f} daily ranges).")
    gaps = []
    if b["tight"] > 0.85:
        gaps.append("the range is still wide")
    if b["dist"] > 1.0:
        gaps.append(f"it is {b['dist']:.1f} daily ranges below the trigger")
    if not b["ma_ok"]:
        gaps.append("it is under its 20-day average")
    return "Not ready: " + " and ".join(gaps) + "." if gaps else "Close to ready."


def analyse(bars: dict[str, pd.DataFrame], meta: pd.DataFrame, cfg: dict) -> dict:
    """Everything the report shows, from price panels. Pure function of its inputs."""
    asof = bars["Close"].index[-1]
    end = len(bars["Close"]) - 1
    skip = set(C.INDEXES) | set(C.GROUPS)
    m = screen.metrics(bars, cfg, skip)
    br = screen.breadth(bars, m)
    reg = regime.assess({t: bars["Close"][t] for t in C.INDEXES}, br)
    grp = screen.groups(bars, C.GROUPS)
    info = meta.set_index("symbol").to_dict("index") if len(meta) else {}
    no_trade = set(cfg["no_trade"])
    sf = reg["size_factor"]

    def common(t: str) -> dict:
        r = m.loc[t]
        i = info.get(t, {})
        return {
            "ticker": t, "name": i.get("name", ""), "sector": i.get("sector", ""),
            "industry": i.get("industry", ""), "close": float(r["close"]),
            "chg": float(r["chg"]), "adr_pct": float(r["adr"] * 100),
            "dollar_vol": float(r["dollar_vol"]),
            "rs": None if pd.isna(r["rs"]) else float(r["rs"]),
            "p1m": None if pd.isna(r["p1m"]) else float(r["p1m"]),
            "p3m": None if pd.isna(r["p3m"]) else float(r["p3m"]),
            "_chart": _chart(bars, t),
        }

    # --- Episodic pivots: any liquid stock with a recent big gap ----------------
    eps, blocked = [], []
    e = cfg["episodic_pivot"]
    o, c = bars["Open"], bars["Close"]
    gaps = (o / c.shift(1) - 1).iloc[-(e["lookback_days"] + 1):].max()
    for t in m.index[m["liquid"] & (gaps.reindex(m.index) >= e["min_gap_pct"] / 100)]:
        try:
            ep = setups.detect_ep(*_arrays(bars, t), end, cfg)
        except Exception as err:
            _log(f"{t}: episodic pivot check failed ({err})")
            continue
        if ep is None:
            continue
        if t in no_trade:
            blocked.append(t)
            continue
        sc = setups.score_ep(ep, m.loc[t, "dollar_vol"])
        entry = ep["pivot"] * 1.001
        row = {**common(t), "setup": "ep", "status": "today" if ep["age"] == 0 else "holding",
               **sc, "grade": setups.grade(sc["score"]), "pivot": ep["pivot"],
               "plan": setups.plan(entry, ep["recent_low"], ep["adr"], cfg, max(sf, 0.5)),
               "gap_date": bars["Close"].index[end - ep["age"]].strftime("%Y-%m-%d"),
               "facts": {k: ep[k] for k in ("age", "gap", "vol_mult", "close_pos", "prior_3m",
                                            "clears_6m_high", "gap_low", "dist")}}
        when = "today" if ep["age"] == 0 else f"{ep['age']} session{'s' if ep['age'] > 1 else ''} ago"
        row["headline"] = f"Gapped up {_pct(ep['gap'])} {when} on {ep['vol_mult']:.1f}x normal volume"
        row["why"] = [
            f"Closed in the top {_pct(1 - ep['close_pos'])} of the gap day's range."
            if ep["close_pos"] >= 0.5 else
            f"Closed in the lower half of the gap day's range ({_pct(ep['close_pos'])} up from the low).",
            (f"Was down {_pct(-ep['prior_3m'])} over the three months before: a fresh move."
             if ep["prior_3m"] < 0 else
             f"Was up only {_pct(ep['prior_3m'])} in the three months before: a fresh move."
             if ep["prior_3m"] < 0.3 else
             f"Had already run {_pct(ep['prior_3m'])} in the three months before: less fresh."),
            "Cleared its six-month high." if ep["clears_6m_high"] else "Still below its six-month high.",
        ]
        below = ep["pivot"] / ep["close"] - 1
        row["call"] = ("Gap day. The entry was the opening range high; from here it needs a "
                       "tight hold near the high." if ep["age"] == 0 else
                       f"Holding {_pct(below, 1)} below its high since the gap. "
                       "Buyable on a break of that high.")
        eps.append(row)
    ep_names = {r["ticker"] for r in eps}

    # --- Breakouts: leaders only ---------------------------------------------------
    ready, building, fired = [], [], []
    for t in m.index[m["leader"]]:
        if t in ep_names:
            continue
        arr = _arrays(bars, t)
        try:
            hit = setups.detect_breakout_today(*arr, end, cfg)
            base = None if hit else setups.detect_base(*arr, end, cfg)
        except Exception as err:
            _log(f"{t}: base check failed ({err})")
            continue
        if hit is None and base is None:
            continue
        if t in no_trade:
            blocked.append(t)
            continue
        r = m.loc[t]
        if hit:
            sc = setups.score_base(hit, r["rs"], r["dollar_vol"])
            p = setups.plan(hit["close"], hit["low_of_day"], hit["adr"], cfg, sf, fixed_stop=True)
            late = hit["extension"] > 1.0
            if late or p["stop_too_wide"]:   # no size on an entry that has already gone
                p.update(shares=0, position_pct=0.0, risk_usd=0, no_size_reason="wait for a new entry")
            row = {**common(t), "setup": "breakout", "status": "fired", **sc,
                   "grade": setups.grade(sc["score"]), "pivot": hit["pivot"], "plan": p,
                   "facts": {k: hit[k] for k in ("leg_gain", "leg_days", "base_days", "retrace",
                                                 "tight", "vol_ratio", "vol_mult", "extension",
                                                 "close_pos", "day_gain")}}
            row["headline"] = (f"Broke out of a {hit['base_days']}-day base today, "
                               f"up {_pct(hit['day_gain'], 1)} on {hit['vol_mult']:.1f}x volume")
            row["why"] = _why_base(hit)[:2] + [
                f"Closed {hit['extension']:.1f} daily ranges above the trigger, "
                f"in the top {_pct(1 - hit['close_pos'])} of the day's range."]
            row["call"] = ("Too far above the trigger to chase. Wait for it to rest on the 10-day."
                           if late else "Stop wider than one daily range: the entry has gone. Wait."
                           if p["stop_too_wide"] else
                           "Still buyable near the close, with the stop at today's low.")
            fired.append(row)
            continue
        status = setups.base_status(base)
        sc = setups.score_base(base, r["rs"], r["dollar_vol"])
        entry = base["pivot"] * 1.001
        recent_low = float(np.nanmin(arr[2][end - 2:end + 1]))
        row = {**common(t), "setup": "breakout", "status": status, **sc,
               "grade": setups.grade(sc["score"]), "pivot": base["pivot"],
               "plan": setups.plan(entry, recent_low, base["adr"], cfg, sf),
               "facts": {k: base[k] for k in ("leg_gain", "leg_days", "base_days", "retrace",
                                              "depth", "tight", "vol_ratio", "dist", "ma_stack",
                                              "higher_lows")}}
        row["headline"] = (f"{base['base_days']}-day base after a {_pct(base['leg_gain'])} run")
        row["why"] = _why_base(base)
        row["call"] = _base_call(base, status)
        (ready if status == "ready" else building).append(row)

    rep = cfg["report"]
    for lst in (ready, building, fired, eps):
        lst.sort(key=lambda r: r["score"], reverse=True)
    ready = [r for r in ready if r["score"] >= rep["min_score"]][:rep["max_ready"]]
    building = [r for r in building if r["score"] >= rep["min_score"] - 5][:rep["max_building"]]
    eps = eps[:rep["max_episodic"]]

    return {
        "asof": asof.strftime("%Y-%m-%d"),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "regime": reg, "breadth": br, "groups": grp,
        "ready": ready, "fired": fired, "episodic": eps, "building": building,
        "blocked": sorted(set(blocked)),
        "counts": {"scanned": int(m["close"].notna().sum()), "liquid": int(m["liquid"].sum()),
                   "leaders": int(m["leader"].sum())},
        "account": cfg["account"],
        "_index_chart": _chart(bars, "QQQ", 110),
    }


# ---------------------------------------------------------------------------
# History: what changed since the last run, and a log to measure the scanner by
# ---------------------------------------------------------------------------
def compare_with_previous(payload: dict, bars: dict[str, pd.DataFrame]) -> None:
    path = C.DATA / "latest.json"
    payload["followup"] = None
    if not path.exists():
        return
    try:
        prev = json.loads(path.read_text())
    except Exception:
        return
    if prev.get("demo") != payload.get("demo") or prev.get("asof", "") >= payload["asof"]:
        return
    seen = {r["ticker"] for k in ("ready", "building") for r in prev.get(k, [])}
    for r in payload["ready"]:
        r["new"] = r["ticker"] not in seen
    high = bars["High"].iloc[-1]
    triggered, waiting = [], []
    for r in prev.get("ready", []):
        t = r["ticker"]
        if t in high and pd.notna(high[t]) and high[t] > r["pivot"]:
            triggered.append(t)
        else:
            waiting.append(t)
    payload["followup"] = {"date": prev["asof"], "triggered": triggered, "waiting": waiting}


def save(payload: dict) -> None:
    C.DATA.mkdir(exist_ok=True)

    def plain(x):   # numpy types and NaN are not valid JSON
        if isinstance(x, dict):
            return {k: plain(v) for k, v in x.items()}
        if isinstance(x, list):
            return [plain(v) for v in x]
        if isinstance(x, (np.floating, np.integer, np.bool_)):
            x = x.item()
        if isinstance(x, float) and x != x:
            return None
        return x

    # Chart bars are kept in the file, so the page can be rebuilt from it without a download
    keep = {k: v for k, v in payload.items() if k != "open_check"}
    (C.DATA / "latest.json").write_text(json.dumps(plain(keep), separators=(",", ":")))
    if payload.get("demo"):
        return
    rows = []
    for key in ("ready", "fired", "episodic", "building"):
        for r in payload[key]:
            rows.append({"date": payload["asof"], "ticker": r["ticker"], "setup": r["setup"],
                         "status": r["status"], "score": r["score"], "grade": r["grade"],
                         "regime": payload["regime"]["state"], "close": round(r["close"], 2),
                         "trigger": round(r["pivot"], 2), "stop": r["plan"]["stop"]})
    log = C.DATA / "history.csv"
    new = pd.DataFrame(rows)
    if log.exists():
        old = pd.read_csv(log)
        new = pd.concat([old[old["date"] != payload["asof"]], new], ignore_index=True)
    new.to_csv(log, index=False)


def attach_earnings(payload: dict) -> None:
    """Next report date for every stock shown, and the result behind each gap."""
    from datetime import date

    from . import earnings
    asof = date.fromisoformat(payload["asof"])
    main = [r for k in ("ready", "fired", "episodic") for r in payload[k]]
    try:
        cal = earnings.load(asof)
        source = "nasdaq calendar"
        if cal["health"]["earnings_days_read"] == 0:      # calendar down: ask Yahoo per stock
            y = earnings.from_yahoo([r["ticker"] for r in main], asof)
            cal["upcoming"], cal["recent"] = y["upcoming"], y["recent"]
            source = "yahoo" if (y["upcoming"] or y["recent"]) else "none"
    except Exception as err:                              # never let this stop the scan
        _log(f"earnings lookup failed ({err})")
        cal, source = {"upcoming": {}, "recent": {}, "health": {}}, "none"
    for r in main + payload["building"]:
        r["earnings"] = cal["upcoming"].get(r["ticker"])
    found = 0
    for r in payload["episodic"]:
        cat = earnings.catalyst(cal["recent"], r["ticker"], r["gap_date"])
        r["catalyst"] = cat
        found += cat is not None
        r["catalyst_text"] = (earnings.describe(cat) if cat else
                              "No earnings report on the gap day: check the news for the cause."
                              if source != "none" else
                              "Earnings data was not available: check what caused the gap.")
    payload["health"].update(cal["health"], earnings_source=source, gaps_on_earnings=found)


def attach_open_check(payload: dict) -> None:
    """Show the after-open check while it still belongs to this sheet."""
    path = C.DATA / "open_check.json"
    payload.pop("open_check", None)
    if path.exists():
        try:
            chk = json.loads(path.read_text())
        except Exception:
            return
        if chk.get("for_asof") == payload["asof"]:
            payload["open_check"] = chk


def run(demo: bool = False) -> dict:
    cfg = C.load()
    health = {}
    if demo:
        from . import demo as D
        bars, meta = D.make()
        source = "invented demo data"
    else:
        from . import fetch, universe
        u = cfg["universe"]
        meta, source = universe.load(C.DATA / "universe.json", u["min_price"], u["min_dollar_volume"])
        tickers = list(C.INDEXES) + list(C.GROUPS) + meta["symbol"].tolist()
        bars, health = fetch.fetch_bars(tickers, C.HISTORY_DAYS)
        if not bars or "QQQ" not in bars["Close"]:
            raise RuntimeError("Price download failed: no data for QQQ. Nothing was published.")
        if health["received"] < 0.6 * health["requested"]:
            raise RuntimeError(f"Price download too thin ({health['received']} of "
                               f"{health['requested']} tickers). Nothing was published.")
        bars = fetch.align(bars)

    payload = analyse(bars, meta, cfg)
    payload["demo"] = demo
    payload["health"] = {"stock_list": source, **health}

    if not demo:
        attach_earnings(payload)

    compare_with_previous(payload, bars)
    attach_open_check(payload)

    from . import render
    C.SITE.mkdir(exist_ok=True)
    (C.SITE / "index.html").write_text(render.page(payload), encoding="utf-8")
    save(payload)
    _log(f"{payload['asof']}: regime {payload['regime']['state']}, {len(payload['ready'])} ready, "
         f"{len(payload['fired'])} fired, {len(payload['episodic'])} episodic pivots, "
         f"{len(payload['building'])} building")
    return payload


def rerender() -> None:
    """Rebuild the page from the last saved scan (no download)."""
    from . import render
    payload = json.loads((C.DATA / "latest.json").read_text())
    attach_open_check(payload)
    C.SITE.mkdir(exist_ok=True)
    (C.SITE / "index.html").write_text(render.page(payload), encoding="utf-8")
    _log(f"page rebuilt from the {payload['asof']} scan")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="use invented data (no network)")
    ap.add_argument("--render", action="store_true", help="rebuild the page from data/latest.json")
    args = ap.parse_args()
    rerender() if args.render else run(args.demo)

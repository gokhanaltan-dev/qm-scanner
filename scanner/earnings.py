"""Earnings dates and results, from Nasdaq's public earnings calendar.

Two questions are answered for the stocks on the sheet:
  * is an earnings report coming in the next two weeks (a fresh breakout has no cushion)?
  * did a recent gap come on earnings, and by how much did the result beat or miss?

Everything here is best effort. A failed request leaves the answer blank; it never
stops the scan. `health` records how much came back so a silent failure is visible.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import requests

from .universe import UA

CALENDAR = "https://api.nasdaq.com/api/calendar/earnings?date={day}"


def _log(msg: str) -> None:
    print(f"[earnings] {msg}", flush=True)


def _money(x) -> float | None:
    """'$0.42' -> 0.42, '($0.10)' -> -0.10, 'N/A' -> None"""
    s = str(x or "").strip().replace("$", "").replace(",", "")
    if not s or s.upper() in {"N/A", "NA", "--", "-"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    try:
        v = float(s.strip("()"))
    except ValueError:
        return None
    return -v if neg else v


def _sym(s: str) -> str:
    return str(s or "").strip().upper().replace(".", "-").replace("/", "-")


def business_days(start: date, n: int, step: int) -> list[date]:
    """n weekdays from start (exclusive), forwards (step=1) or backwards (step=-1)."""
    out, d = [], start
    while len(out) < n:
        d += timedelta(days=step)
        if d.weekday() < 5:
            out.append(d)
    return out


def parse_day(payload: dict, day: date) -> list[dict]:
    rows = ((payload or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        sym = _sym(r.get("symbol"))
        if not sym:
            continue
        eps, est = _money(r.get("eps")), _money(r.get("epsForecast"))
        surprise = _money(r.get("surprise"))
        if surprise is None and eps is not None and est not in (None, 0):
            surprise = (eps - est) / abs(est) * 100
        when = str(r.get("time") or "")
        out.append({"symbol": sym, "date": day.isoformat(), "eps": eps, "forecast": est,
                    "surprise": surprise,
                    "time": "before the open" if "pre" in when else
                            "after the close" if "after" in when else ""})
    return out


def fetch_day(day: date, session=None) -> list[dict]:
    r = (session or requests).get(CALENDAR.format(day=day.isoformat()), headers=UA, timeout=15)
    r.raise_for_status()
    return parse_day(r.json(), day)


def load(asof: date, back: int = 7, ahead: int = 12, pause: float = 0.3) -> dict:
    """Earnings around `asof` (the session the scan is for).

    Returns {"upcoming": {symbol: iso date}, "recent": {symbol: row}, "health": {...}}.
    """
    past = [asof] + business_days(asof, back, -1)
    future = business_days(asof, ahead, 1)
    upcoming: dict[str, str] = {}
    recent: dict[str, dict] = {}
    ok = failed = rows = 0
    s = requests.Session()
    for day in past + future:
        if failed >= 3 and ok == 0:          # the source is not answering: stop asking
            break
        try:
            got = fetch_day(day, s)
            ok += 1
        except Exception as e:
            failed += 1
            _log(f"{day}: {e}")
            continue
        rows += len(got)
        for g in got:
            if day > asof:
                upcoming.setdefault(g["symbol"], g["date"])
            else:
                # keep the most recent report per stock (past is listed newest first)
                recent.setdefault(g["symbol"], g)
        time.sleep(pause)
    health = {"earnings_days_read": ok, "earnings_days_failed": failed, "earnings_rows": rows}
    _log(str(health))
    return {"upcoming": upcoming, "recent": recent, "health": health}


def catalyst(recent: dict, symbol: str, gap_date: str) -> dict | None:
    """The earnings report behind a gap, if one was published on the gap day or the
    weekday before it (after the close)."""
    r = recent.get(symbol)
    if not r:
        return None
    g = date.fromisoformat(gap_date)
    prev = business_days(g, 1, -1)[0]
    if r["date"] not in (g.isoformat(), prev.isoformat()):
        return None
    return r


def describe(cat: dict) -> str:
    eps, est, sur = cat.get("eps"), cat.get("forecast"), cat.get("surprise")
    if eps is None or est is None:
        return "The gap came on an earnings report. The result was not in the calendar: check it."
    verdict = "beat" if eps > est else "missed" if eps < est else "matched"

    def usd(x):
        return f"{'−' if x < 0 else ''}${abs(x):.2f}"

    extra = f" ({sur:+.0f}%)".replace("-", "−") if sur is not None and verdict != "matched" else ""
    return (f"The gap came on earnings: {usd(eps)} a share {verdict} the {usd(est)} expected"
            f"{extra}. Check revenue and guidance.")


def from_yahoo(tickers: list[str], asof: date) -> dict:
    """Fallback for single stocks: next report date and the latest result, from Yahoo."""
    out = {"upcoming": {}, "recent": {}}
    try:
        import yfinance as yf
    except Exception:
        return out
    for t in tickers:
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=8)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            continue
        for ts, row in df.sort_index().iterrows():
            d = ts.date()
            if d > asof:
                out["upcoming"].setdefault(t, d.isoformat())
            elif (asof - d).days <= 12:
                def num(k):
                    v = row.get(k)
                    return None if v is None or v != v else float(v)
                out["recent"][t] = {"symbol": t, "date": d.isoformat(), "eps": num("Reported EPS"),
                                    "forecast": num("EPS Estimate"), "surprise": num("Surprise(%)"),
                                    "time": "after the close" if ts.hour >= 15 else "before the open"}
        time.sleep(0.3)
    return out

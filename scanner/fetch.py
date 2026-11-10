"""Daily bars from Yahoo (via yfinance), downloaded in batches.

Prices are split-adjusted but not dividend-adjusted, so levels match a default
TradingView chart. One failed batch never stops the run: it is retried, and whatever
is still missing is recorded in `health`.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

FIELDS = ("Open", "High", "Low", "Close", "Volume")


def _log(msg: str) -> None:
    print(f"[fetch] {msg}", flush=True)


def _batch(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    df = yf.download(
        tickers, start=start, interval="1d", auto_adjust=False, progress=False,
        threads=True, group_by="column", multi_level_index=True, timeout=30,
    )
    if df is None or df.empty or not isinstance(df.columns, pd.MultiIndex):
        return {}
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    out = {}
    for f in FIELDS:
        if f in df.columns.get_level_values(0):
            out[f] = df[f].dropna(how="all", axis=1)
    return out


def fetch_bars(tickers: list[str], days: int, chunk: int = 200, pause: float = 1.0):
    """Return ({field: DataFrame[dates x tickers]}, health)."""
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    parts: dict[str, list[pd.DataFrame]] = {f: [] for f in FIELDS}
    missing: list[str] = []
    chunks = [tickers[i:i + chunk] for i in range(0, len(tickers), chunk)]
    for n, group in enumerate(chunks, 1):
        want = list(group)
        for attempt in range(3):
            try:
                got = _batch(want, start)
            except Exception as e:  # network / rate limit
                _log(f"batch {n}/{len(chunks)} attempt {attempt + 1} failed: {e}")
                got = {}
            if got and "Close" in got:
                ok = [t for t in got["Close"].columns if got["Close"][t].notna().sum() >= 30]
                for f in FIELDS:
                    if f in got:
                        parts[f].append(got[f].reindex(columns=ok))
                want = [t for t in want if t not in ok]
            # Tickers Yahoo simply does not have are not worth retrying at length
            if not want or (attempt >= 1 and len(want) < 0.2 * len(group)):
                break
            time.sleep(4 * (attempt + 1))
        missing.extend(want)
        if n % 5 == 0 or n == len(chunks):
            _log(f"{n}/{len(chunks)} batches done")
        time.sleep(pause)

    bars = {}
    for f in FIELDS:
        if parts[f]:
            df = pd.concat(parts[f], axis=1)
            bars[f] = df.loc[:, ~df.columns.duplicated()].sort_index()
    health = {"requested": len(tickers), "received": int(bars["Close"].shape[1]) if bars else 0,
              "missing": len(missing)}
    return bars, health


def align(bars: dict[str, pd.DataFrame], benchmark: str = "QQQ") -> dict[str, pd.DataFrame]:
    """Use the benchmark's sessions as the calendar, and drop today's bar if the US
    market has not closed yet (a half-finished bar would fake tight ranges and low volume)."""
    sessions = bars["Close"][benchmark].dropna().index
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if len(sessions) and sessions[-1].date() == now_et.date() and now_et.hour * 60 + now_et.minute < 16 * 60 + 10:
        _log("US market still open: ignoring today's unfinished bar")
        sessions = sessions[:-1]
    return {f: df.reindex(sessions) for f, df in bars.items()}

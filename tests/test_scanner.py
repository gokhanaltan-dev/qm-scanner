"""Offline tests: detection logic on constructed price paths, the full build on
invented data, and the download code with Yahoo's answer faked."""
import numpy as np
import pandas as pd
import pytest

from scanner import build, config as C, demo, fetch, regime, render, setups, universe

CFG = C.load()
RS = np.random.default_rng(3)
END = demo.N - 1


def test_tight_flag_is_ready():
    arr = demo.flag(RS, base_days=8, run=0.8, final_gap=0.01)
    base = setups.detect_base(*arr, END, CFG)
    assert base is not None
    assert base["base_days"] in (7, 8)   # the peak can print on the first base day
    assert base["leg_gain"] > 0.6
    assert base["tight"] < 0.7 and base["vol_ratio"] < 1
    assert setups.base_status(base) == "ready"
    assert setups.score_base(base, 99, 5e7)["score"] >= 80


def test_loose_or_distant_base_is_only_building():
    loose = setups.detect_base(*demo.flag(RS, base_days=6, run=0.6, final_gap=0.03, tight=False), END, CFG)
    far = setups.detect_base(*demo.flag(RS, base_days=7, run=0.7, final_gap=0.09, pullback=0.12), END, CFG)
    assert loose is not None and setups.base_status(loose) == "building"
    assert far is not None and setups.base_status(far) == "building"
    tight = setups.detect_base(*demo.flag(RS, base_days=8, run=0.8, final_gap=0.01), END, CFG)
    assert setups.score_base(tight, 99, 5e7)["score"] > setups.score_base(loose, 99, 5e7)["score"]


def test_no_base_without_a_run_or_in_a_downtrend():
    n = demo.N
    flat = demo._ohlcv(demo._walk(RS, n, 50, 0.0, 0.006), np.full(n, 0.03), np.full(n, 1e6), RS)
    down = demo._ohlcv(demo._walk(RS, n, 50, -0.004, 0.01), np.full(n, 0.05), np.full(n, 1e6), RS)
    assert setups.detect_base(*flat, END, CFG) is None
    assert setups.detect_base(*down, END, CFG) is None


def test_deep_pullback_is_rejected():
    arr = demo.flag(RS, base_days=8, run=0.6, final_gap=0.25, pullback=0.30)
    assert setups.detect_base(*arr, END, CFG) is None


def test_breakout_today_needs_yesterdays_base_and_volume():
    o, h, l, c, v = demo.flag(RS, base_days=9, run=0.75, final_gap=0.012, today_breakout=True)
    hit = setups.detect_breakout_today(o, h, l, c, v, END, CFG)
    assert hit is not None and hit["close"] > hit["pivot"] and hit["vol_mult"] > 1.3
    quiet = v.copy()
    quiet[END] = np.mean(v[END - 50:END]) * 0.8   # same price move on weak volume
    assert setups.detect_breakout_today(o, h, l, c, quiet, END, CFG) is None
    # the day before, it was a ready base rather than a breakout
    assert setups.detect_breakout_today(o, h, l, c, v, END - 1, CFG) is None
    assert setups.base_status(setups.detect_base(o, h, l, c, v, END - 1, CFG)) == "ready"


def test_episodic_pivot_today_and_holding():
    ep = setups.detect_ep(*demo.episodic(RS, age=0, gap=0.2), END, CFG)
    assert ep is not None and ep["age"] == 0 and ep["gap"] == pytest.approx(0.2, abs=0.01)
    assert ep["vol_mult"] > 5
    held = setups.detect_ep(*demo.episodic(RS, age=2, gap=0.13), END, CFG)
    assert held is not None and held["age"] == 2
    # a gap that is later undercut no longer counts
    o, h, l, c, v = demo.episodic(RS, age=2, gap=0.13)
    l = l.copy()
    l[END] = l[END - 2] * 0.97
    assert setups.detect_ep(o, h, l, c, v, END, CFG) is None
    # ... and neither does one that has faded into the lower half of its range since
    o, h, l, c, v = demo.episodic(RS, age=2, gap=0.13)
    h, c = h.copy(), c.copy()
    h[END - 1] = c[END - 2] * 1.30
    c[END] = l[END - 2] * 1.02
    assert setups.detect_ep(o, h, l, c, v, END, CFG) is None
    # a small gap on normal volume is not an episodic pivot
    n = demo.N
    dull = demo._ohlcv(demo._walk(RS, n, 40, 0.0, 0.01), np.full(n, 0.03), np.full(n, 1e6), RS,
                       gaps={END: 0.03})
    assert setups.detect_ep(*dull, END, CFG) is None


def test_plan_sizes_to_the_risk_budget_and_caps_position():
    cfg = C._merge(CFG, {"account": {"size_usd": 20000, "risk_per_trade_pct": 0.5, "max_position_pct": 20}})
    p = setups.plan(entry=50.0, stop_ref=48.0, adr=0.05, cfg=cfg, size_factor=1.0)
    assert p["stop"] == 48.0 and p["shares"] == 50 and p["risk_usd"] == 100
    # stop is never planned wider than one average daily range
    p = setups.plan(entry=50.0, stop_ref=40.0, adr=0.05, cfg=cfg, size_factor=1.0)
    assert p["stop"] == 47.5
    # ... nor closer than 0.4 of one
    p = setups.plan(entry=50.0, stop_ref=49.9, adr=0.05, cfg=cfg, size_factor=1.0)
    assert p["stop"] == 49.0
    # a very tight stop would oversize the position: the cap wins
    p = setups.plan(entry=50.0, stop_ref=49.9, adr=0.01, cfg=cfg, size_factor=1.0)
    assert p["capped"] and p["position_pct"] <= 20
    # red light: no size
    assert setups.plan(50.0, 48.0, 0.05, cfg, 0.0)["shares"] == 0
    # amber: half the risk
    assert setups.plan(50.0, 48.0, 0.05, cfg, 0.5)["shares"] == 25


def test_regime_states():
    idx = pd.bdate_range(end="2026-11-06", periods=120)
    up = pd.Series(np.linspace(100, 140, 120), index=idx)
    down = pd.Series(np.linspace(140, 100, 120), index=idx)
    # a sharp three-day drop: price under the 20-day while the 10-day is still above it
    turn = pd.Series(np.r_[np.linspace(100, 140, 117), [137, 135, 134]], index=idx)
    assert regime.index_state(up)["state"] == "green"
    assert regime.index_state(down)["state"] == "red"
    assert regime.index_state(turn)["state"] == "amber"
    br = {"above20": 30.0, "followthrough": 55.0}
    r = regime.assess({"QQQ": up, "SPY": up, "IWM": up}, br)
    assert r["state"] == "amber" and r["size_factor"] == 0.5   # narrow breadth downgrades green
    r = regime.assess({"QQQ": down, "SPY": up, "IWM": up}, {"above20": 80.0, "followthrough": 80.0})
    assert r["state"] == "red" and r["size_factor"] == 0.0


def test_full_build_on_invented_data(tmp_path, monkeypatch):
    bars, meta = demo.make()
    cfg = C._merge(CFG, {"no_trade": ["FLAG2"]})
    cfg["no_trade"] = ["FLAG2"]
    p = build.analyse(bars, meta, cfg)
    ready = [r["ticker"] for r in p["ready"]]
    assert "FLAG1" in ready and "FLAG3" in ready
    assert "FLAG2" not in ready and p["blocked"] == ["FLAG2"]
    assert [r["ticker"] for r in p["fired"]] == ["TRIG1"]
    assert {r["ticker"] for r in p["episodic"]} == {"EPIC1", "EPIC2"}
    assert {"BUILD1", "BUILD2"} <= {r["ticker"] for r in p["building"]}
    for key in ("ready", "fired", "episodic", "building"):
        scores = [r["score"] for r in p[key]]
        assert scores == sorted(scores, reverse=True)
        for r in p[key]:
            assert 0 <= r["score"] <= 100
            assert r["plan"]["stop"] < r["plan"]["entry"]
            assert r["plan"]["position_pct"] <= cfg["account"]["max_position_pct"]
    p["demo"], p["health"], p["followup"] = True, {"stock_list": "test"}, None
    html = render.page(p)
    assert "FLAG1" in html and "Ready to trigger" in html and "<svg" in html


def test_followup_reports_yesterdays_triggers(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA", tmp_path)
    bars, meta = demo.make()
    yesterday = {f: df.iloc[:-1] for f, df in bars.items()}
    prev = build.analyse(yesterday, meta, CFG)
    prev["demo"] = True
    assert "TRIG1" in [r["ticker"] for r in prev["ready"]]   # ready the day before it fired
    build.save(prev)
    today = build.analyse(bars, meta, CFG)
    today["demo"] = True
    build.compare_with_previous(today, bars)
    assert "TRIG1" in today["followup"]["triggered"]
    assert not (tmp_path / "history.csv").exists()            # demo runs never touch the log


def test_universe_cleaning():
    df = universe._from_rows([
        {"symbol": "ABC", "name": "ABC Corp Common Stock", "lastsale": "$25.10", "volume": "2000000"},
        {"symbol": "ABCW", "name": "ABC Corp Warrant", "lastsale": "$1.00", "volume": "1000"},
        {"symbol": "XYZ^A", "name": "XYZ 6.5% Preferred", "lastsale": "$24.00", "volume": "1000"},
        {"symbol": "BRK/B", "name": "Berkshire Hathaway Class B", "lastsale": "$480.00", "volume": "3000000"},
        {"symbol": "PENY", "name": "Penny Co Common Stock", "lastsale": "$0.40", "volume": "9000000"},
        {"symbol": "DEADU", "name": "Dead SPAC Units", "lastsale": "$10.00", "volume": "100"},
    ])
    c = universe.clean(df)
    assert c["symbol"].tolist() == ["ABC", "BRK-B", "PENY"]
    assert universe.prefilter(c, 5, 10e6)["symbol"].tolist() == ["ABC", "BRK-B"]


def test_download_path_with_yahoo_faked(monkeypatch):
    """Exercise chunking, retries and the panel layout without touching the network."""
    import yfinance as yf
    bars, _ = demo.make()
    tickers = ["QQQ", "FLAG1", "N001", "N002", "GONE"]
    calls = []

    def fake_download(tk, **kw):
        calls.append(list(tk))
        have = [t for t in tk if t in bars["Close"]]
        if not have:
            return pd.DataFrame()
        frames = {f: bars[f][have] for f in fetch.FIELDS}
        df = pd.concat(frames, axis=1)
        df.index = df.index.tz_localize("America/New_York")
        return df

    monkeypatch.setattr(yf, "download", fake_download)
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    got, health = fetch.fetch_bars(tickers, days=460, chunk=2)
    assert set(got) == set(fetch.FIELDS)
    assert sorted(got["Close"].columns) == ["FLAG1", "N001", "N002", "QQQ"]
    assert health == {"requested": 5, "received": 4, "missing": 1}
    assert got["Close"].index.tz is None
    aligned = fetch.align(got)
    pd.testing.assert_series_equal(aligned["High"]["FLAG1"].dropna(), bars["High"]["FLAG1"].loc[aligned["High"].index],
                                   check_freq=False)


def test_saved_stock_list_is_used_when_every_source_fails(tmp_path, monkeypatch):
    import itertools
    syms = ["".join(c) for c in itertools.product("ABCDEFGHJKLMN", repeat=3)][:2100]
    rows = [{"symbol": t, "name": "Co Common Stock", "sector": "Technology",
             "industry": "Software", "lastsale": "$20", "volume": "5000000"} for t in syms]
    monkeypatch.setattr(universe, "_screener", lambda: universe._from_rows(rows))
    cache = tmp_path / "universe.json"
    df, src = universe.load(cache, 5, 10e6)
    assert src == "nasdaq screener" and len(df) == 2100
    assert "lastsale" not in cache.read_text() and '"last"' not in cache.read_text()

    def boom():
        raise RuntimeError("offline")
    for fn in ("_screener", "_mirror", "_symdir"):
        monkeypatch.setattr(universe, fn, boom)
    df, src = universe.load(cache, 5, 10e6)
    assert src == "saved list" and len(df) == 2100


def test_page_can_be_rebuilt_from_the_saved_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA", tmp_path / "data")
    monkeypatch.setattr(C, "SITE", tmp_path / "site")
    bars, meta = demo.make()
    bars["Close"].iloc[:280, bars["Close"].columns.get_loc("EPIC1")] = np.nan   # a recent listing
    p = build.analyse(bars, meta, CFG)
    p["demo"], p["health"], p["followup"] = True, {"stock_list": "test"}, None
    build.attach_backtest(p)
    first = render.page(p)
    assert ("What the backtest found" in first) == (p["backtest"] is not None)
    build.save(p)
    import json
    json.loads((tmp_path / "data" / "latest.json").read_text(), parse_constant=lambda c: 1 / 0)  # strict JSON
    build.rerender()
    assert (tmp_path / "site" / "index.html").read_text() == first


# ---------------------------------------------------------------------------
# Earnings calendar
# ---------------------------------------------------------------------------
def test_earnings_calendar_parsing_and_gap_matching():
    from datetime import date
    from scanner import earnings
    past = {"data": {"rows": [
        {"symbol": "ABC", "eps": "$0.52", "epsForecast": "$0.41", "surprise": "26.83", "time": "time-after-hours"},
        {"symbol": "BRK.B", "eps": "($0.10)", "epsForecast": "$0.05", "time": "time-pre-market"},
        {"symbol": "NOEPS", "eps": "N/A", "epsForecast": "", "time": "time-not-supplied"},
        {"symbol": ""}]}}
    rows = earnings.parse_day(past, date(2026, 11, 5))
    assert [r["symbol"] for r in rows] == ["ABC", "BRK-B", "NOEPS"]
    assert rows[0]["surprise"] == pytest.approx(26.83) and rows[0]["time"] == "after the close"
    assert rows[1]["eps"] == -0.10 and rows[1]["surprise"] == pytest.approx(-300)
    assert earnings.parse_day({"data": None}, date(2026, 11, 5)) == []
    assert earnings.parse_day({}, date(2026, 11, 5)) == []

    recent = {r["symbol"]: r for r in rows}
    # reported Thursday after the close -> the gap is Friday
    assert earnings.catalyst(recent, "ABC", "2026-11-06")["eps"] == 0.52
    assert earnings.catalyst(recent, "ABC", "2026-11-05") is not None
    assert earnings.catalyst(recent, "ABC", "2026-11-09") is None      # Monday: too late
    assert earnings.catalyst(recent, "ZZZ", "2026-11-06") is None
    text = earnings.describe(recent["ABC"])
    assert "$0.52" in text and "beat" in text and "+27%" in text
    assert "missed" in earnings.describe(recent["BRK-B"])
    assert "not in the calendar" in earnings.describe(recent["NOEPS"])
    # Friday's previous weekday is Thursday; Monday's is Friday
    assert earnings.business_days(date(2026, 11, 9), 1, -1) == [date(2026, 11, 6)]
    assert earnings.business_days(date(2026, 11, 6), 2, 1) == [date(2026, 11, 9), date(2026, 11, 10)]


def test_earnings_load_survives_a_dead_source(monkeypatch):
    from datetime import date
    from scanner import earnings
    calls = []

    def boom(day, session=None):
        calls.append(day)
        raise RuntimeError("blocked")
    monkeypatch.setattr(earnings, "fetch_day", boom)
    monkeypatch.setattr(earnings.time, "sleep", lambda s: None)
    out = earnings.load(date(2026, 11, 9))
    assert out["upcoming"] == {} and out["health"]["earnings_days_read"] == 0
    assert len(calls) == 3                                      # gives up quickly

    def fake(day, session=None):
        return [{"symbol": "ABC", "date": day.isoformat(), "eps": None, "forecast": None,
                 "surprise": None, "time": ""}]
    monkeypatch.setattr(earnings, "fetch_day", fake)
    out = earnings.load(date(2026, 11, 9), back=2, ahead=3)
    assert out["upcoming"]["ABC"] == "2026-11-10"               # the nearest future date
    assert out["recent"]["ABC"]["date"] == "2026-11-09"         # the most recent past one


# ---------------------------------------------------------------------------
# After-open check
# ---------------------------------------------------------------------------
def _five_minute(prices, start="2026-11-10 09:30", volume=20000):
    """prices: (open, high, low, close) per 5-minute bar, New York time."""
    idx = pd.date_range(start, periods=len(prices), freq="5min", tz="America/New_York")
    df = pd.DataFrame(prices, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = volume
    return df


def test_after_open_check_window():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from scanner import intraday
    ny = ZoneInfo("America/New_York")
    assert intraday.due(datetime(2026, 11, 10, 10, 10, tzinfo=ny), "2026-11-09") is None
    assert "window" in intraday.due(datetime(2026, 11, 10, 9, 10, tzinfo=ny), "2026-11-09")
    assert intraday.due(datetime(2026, 11, 10, 11, 10, tzinfo=ny), "2026-11-09") is None
    assert "window" in intraday.due(datetime(2026, 11, 10, 11, 40, tzinfo=ny), "2026-11-09")
    assert intraday.due(datetime(2026, 11, 14, 10, 10, tzinfo=ny), "2026-11-13") == "weekend"
    assert "already" in intraday.due(datetime(2026, 11, 10, 10, 10, tzinfo=ny), "2026-11-10")
    assert "old" in intraday.due(datetime(2026, 11, 20, 10, 10, tzinfo=ny), "2026-11-09")
    # The two scheduled times, 13:50 and 14:50 UTC, with GitHub starting up to 45 minutes late.
    # Winter: only the 14:50 one lands in the window. Summer: the 13:50 one does.
    utc = ZoneInfo("UTC")
    for late in (0, 20, 45):
        w1 = datetime(2026, 11, 10, 13, 50, tzinfo=utc) + pd.Timedelta(minutes=late)
        w2 = datetime(2026, 11, 10, 14, 50, tzinfo=utc) + pd.Timedelta(minutes=late)
        assert intraday.due(w1, "2026-11-09") is not None and intraday.due(w2, "2026-11-09") is None
        s1 = datetime(2026, 7, 14, 13, 50, tzinfo=utc) + pd.Timedelta(minutes=late)
        assert intraday.due(s1, "2026-07-13") is None
    assert render.usual_volume(40) == 15 and render.usual_volume(80) == 25 and render.usual_volume(390) == 100


def test_after_open_check_end_to_end(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from scanner import intraday
    monkeypatch.setattr(C, "DATA", tmp_path / "data")
    monkeypatch.setattr(C, "SITE", tmp_path / "site")
    bars, meta = demo.make(end=pd.Timestamp("2026-11-09"))
    p = build.analyse(bars, meta, CFG)
    p["demo"], p["health"], p["followup"] = True, {"stock_list": "test"}, None
    build.save(p)
    by = {r["ticker"]: r for k in ("ready", "episodic") for r in p[k]}
    f1, f2, f3 = by["FLAG1"]["pivot"], by["FLAG2"]["pivot"], by["FLAG3"]["pivot"]
    fake = {
        # dips first, then goes through the trigger and holds: the stop is the early low
        "FLAG1": _five_minute([(f1 * .97, f1 * .975, f1 * .96, f1 * .97), (f1 * .97, f1 * 1.01, f1 * .97, f1 * 1.008),
                               (f1 * 1.008, f1 * 1.03, f1 * 1.0, f1 * 1.02)]),
        # goes through the trigger and falls back under it
        "FLAG2": _five_minute([(f2 * .995, f2 * 1.01, f2 * .99, f2 * 1.005), (f2 * 1.005, f2 * 1.006, f2 * .992, f2 * .994)]),
        # never gets there
        "FLAG3": _five_minute([(f3 * .96, f3 * .97, f3 * .95, f3 * .965)] * 3),
    }
    monkeypatch.setattr(intraday, "fetch_5m", lambda tickers: {t: fake[t] for t in tickers if t in fake})
    now = datetime(2026, 11, 10, 10, 12, tzinfo=ZoneInfo("America/New_York"))
    out = intraday.run(now)
    rows = {r["ticker"]: r for r in out["rows"]}
    assert out["for_asof"] == "2026-11-09" and out["time_ny"] == "09:45" and out["minutes_in"] == 15
    a = rows["FLAG1"]
    assert a["state"] == "triggered" and a["time"] == "09:35"
    assert a["entry"] == pytest.approx(f1 * 1.001, abs=0.01) and a["stop"] == pytest.approx(f1 * .96, abs=0.01)
    assert a["shares"] > 0 and not a["too_wide"]
    assert rows["FLAG2"]["state"] == "back_under" and rows["FLAG3"]["state"] == "waiting"
    assert [r["ticker"] for r in out["rows"]][:2] == ["FLAG1", "FLAG2"]       # movers first
    assert set(out["missing"]) == {"EPIC1", "EPIC2"}

    build.rerender()
    html = (tmp_path / "site" / "index.html").read_text()
    assert "After the open" in html and "Triggered at 09:35" in html and "Still waiting: FLAG3" in html
    assert "now back under" in html
    # a new evening scan replaces the sheet and the morning check goes with it
    later, _ = demo.make(end=pd.Timestamp("2026-11-10"))
    p2 = build.analyse(later, meta, CFG)
    p2["demo"], p2["health"], p2["followup"] = True, {"stock_list": "test"}, None
    build.attach_open_check(p2)
    assert "open_check" not in p2 and "After the open" not in render.page(p2)
    # outside the window nothing is written
    assert intraday.run(datetime(2026, 11, 10, 12, 30, tzinfo=ZoneInfo("America/New_York"))) is None


def test_thin_or_faded_gaps_are_left_out():
    """The quality rules that came out of the backtest."""
    o, h, l, c, v = demo.episodic(RS, age=0, gap=0.2)
    assert setups.detect_ep(o, h, l, c, v, END, CFG) is not None
    # the same gap in a stock that traded under $5m a day before it
    assert setups.detect_ep(o, h, l, c, v / 10, END, CFG) is None
    # ... or that was under $5 before it
    k = 4.0 / c[END - 1]
    assert setups.detect_ep(o * k, h * k, l * k, c * k, v / k * 2, END, CFG) is None
    # ... or that was already swinging 8% a day
    wild_h, wild_l = h.copy(), l.copy()
    wild_h[END - 25:END] = c[END - 25:END] * 1.05
    wild_l[END - 25:END] = c[END - 25:END] * 0.95
    assert setups.detect_ep(o, wild_h, wild_l, c, v, END, CFG) is None
    # ... or a gap of more than 50%
    assert setups.detect_ep(*demo.episodic(RS, age=0, gap=0.6), END, CFG) is None
    # ... or one that closed well off its high
    far = h.copy()
    far[END] = c[END] * 1.06
    assert setups.detect_ep(o, far, l, c, v, END, CFG) is None
    # with the rules switched off (as the backtest does) the thin one is seen again
    loose = C._merge(CFG, {"episodic_pivot": {"min_pre_dollar_volume": 0}})
    assert setups.detect_ep(o, h, l, c, v / 10, END, loose) is not None
    # near the high earns most of the rating
    ep = setups.detect_ep(o, h, l, c, v, END, CFG)
    at_high = setups.score_ep({**ep, "dist": 0.0}, 5e7)
    off_high = setups.score_ep({**ep, "dist": 0.5}, 5e7)
    assert at_high["parts"]["near"] == 40 and off_high["parts"]["near"] == 0
    assert sum(mx for _, _, mx in setups.EP_PARTS) == 100 == sum(mx for _, _, mx in setups.BREAKOUT_PARTS)

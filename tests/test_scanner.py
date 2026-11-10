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

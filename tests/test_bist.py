"""Unit tests on synthetic bars (no market data)."""
import numpy as np
import pytest

from bist import core

CFG = core.load_config()


def make_stock(breakout=True, vol_mult=2.0, gap=0.001, seed=1):
    rng = np.random.default_rng(seed)
    n_up = 320
    c = list(10 * np.exp(np.linspace(0, 1.0, n_up)))          # steady advance, +170%
    h = [x * 1.008 for x in c]; l = [x * 0.992 for x in c]; o = [x * 0.999 for x in c]
    v = list(1e6 * (1 + 0.1 * rng.random(n_up)))
    top = c[-1] * 1.02
    # 40-session base: wide first 20 sessions (touching the top twice), tight quiet last sessions
    for k in range(40):
        if k in (2, 12, 30):
            cc, hh, ll = top * 0.992, top * (1.0 if k == 2 else 0.997), top * 0.975
        elif k < 20:
            cc = top * (0.92 + 0.03 * np.sin(k)); hh, ll = cc * 1.012, cc * 0.985
        else:
            cc = top * (0.984 + 0.0003 * (k - 20)); hh, ll = cc * 1.004, cc * 0.996
        vv = 1e6 if k < 35 else 5e5
        c.append(cc); h.append(hh); l.append(ll); o.append(cc * 0.999); v.append(vv)
    if breakout:
        cc = top * 1.008
        c.append(cc); h.append(cc * 1.002); l.append(top * 0.985); o.append(top * 0.99); v.append(1e6 * vol_mult)
        nxt = cc * (1 + gap)
        c.append(nxt * 1.01); h.append(nxt * 1.02); l.append(nxt * 0.995); o.append(nxt); v.append(1e6)
        for k in range(30):
            p = c[-1] * 1.004
            c.append(p); h.append(p * 1.006); l.append(p * 0.994); o.append(p * 0.998); v.append(1e6)
        for k in range(10):
            p = c[-1] * 0.97
            c.append(p); h.append(p * 1.01); l.append(p * 0.99); o.append(p * 1.005); v.append(1e6)
    n = len(c)
    a = {"dates": np.arange("2020-01-01", n, dtype="datetime64[D]").astype("datetime64[ns]"),
         "O": np.array(o), "H": np.array(h), "L": np.array(l), "C": np.array(c), "V": np.array(v),
         "open_ok": np.ones(n, bool), "member": np.ones(n, bool), "rs_pct": np.full(n, 95.0),
         "liq_rank": np.full(n, 10.0)}
    return a, top, n_up


def run(a):
    f = core.features(a["O"], a["H"], a["L"], a["C"], a["V"], CFG)
    return f, core.run_tight_base(a, f, CFG, "TEST")


def test_wilder_atr_matches_hand_calculation():
    h = np.array([10, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18, 17, 19, 20, 21.0])
    l = h - 1.0
    c = h - 0.5
    atr = core.wilder_atr(h, l, c, 14)
    pc = np.roll(c, 1)
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    first = tr[1:15].mean()
    assert np.isnan(atr[13]) and atr[14] == pytest.approx(first)
    assert atr[15] == pytest.approx((first * 13 + tr[15]) / 14)


def test_base_arms_then_confirms_and_enters_next_open():
    a, top, n_up = make_stock()
    f, r = run(a)
    confirmed = [s for s in r["setups"] if s["state"] == "CONFIRMED"]
    assert len(confirmed) == 1
    s = confirmed[0]
    assert s["P"] == pytest.approx(top)                       # pivot frozen at the base high
    assert s["end_i"] == n_up + 40                            # the breakout bar
    assert s["armed_i"] < s["end_i"]
    t = r["trades"][0]
    assert t["entry_i"] == s["end_i"] + 1                     # never the confirming close
    assert t["entry"] == pytest.approx(a["O"][t["entry_i"]])
    assert t["stop"] < s["P"] < t["entry"] <= t["max_entry"]
    assert t["reason"] in ("close below EMA20", "stop", "stop (gap through, filled at open)")
    assert t["exit_i"] > t["entry_i"]


def test_weak_volume_does_not_confirm():
    a, _, _ = make_stock(vol_mult=1.2)
    _, r = run(a)
    assert not [s for s in r["setups"] if s["state"] == "CONFIRMED"]


def test_gap_above_max_entry_is_skipped():
    a, _, _ = make_stock(gap=0.06)
    _, r = run(a)
    assert not r["trades"] and r["skips"][0]["reason"] == "open above maximum entry"


def test_expires_after_ten_sessions_without_confirmation():
    a, top, n_up = make_stock(breakout=False)
    # extend the quiet drift for 15 more sessions
    for key, val in (("C", top * 0.99), ("H", top * 0.995), ("L", top * 0.985), ("O", top * 0.99), ("V", 5e5)):
        a[key] = np.concatenate([a[key], np.full(15, val)])
    n = len(a["C"])
    a["dates"] = np.arange("2020-01-01", n, dtype="datetime64[D]").astype("datetime64[ns]")
    for key, val in (("open_ok", True), ("member", True)):
        a[key] = np.full(n, val)
    a["rs_pct"], a["liq_rank"] = np.full(n, 95.0), np.full(n, 10.0)
    _, r = run(a)
    assert any(s["state"] == "EXPIRED" and s["end_i"] - s["armed_i"] == 10 for s in r["setups"])


def test_no_lookahead_truncation():
    a, _, n_up = make_stock()
    _, full = run(a)
    cut = n_up + 38
    b = {k: v[:cut] for k, v in a.items()}
    _, part = run(b)
    f_full = [(s["id"], s["state"]) for s in full["setups"] if s["end_i"] < cut]
    f_part = [(s["id"], s["state"]) for s in part["setups"]]
    assert f_full == f_part
    if part["active"]:
        assert part["active"]["id"] in {s["id"] for s in full["setups"]}


def test_locked_bar_is_not_a_fill():
    a, _, n_up = make_stock()
    e = n_up + 41
    a["H"][e] = a["L"][e] = a["O"][e] = a["C"][e]
    _, r = run(a)
    assert not r["trades"] and "no range" in r["skips"][0]["reason"]


def test_tick_rounding():
    assert core.round_tick(313.76, CFG, "up") == 314.0
    assert core.round_tick(313.76, CFG, "down") == 313.75
    assert core.round_tick(19.994, CFG, "up") == 20.0
    assert core.round_tick(57.33, CFG, "down") == 57.30


def test_score_is_bounded_and_deterministic():
    m = {"sma50_slope": 0.2, "ret126": 2.0, "close_vs_hi252": 1.0, "depth": 0.05, "contr": 0.2, "touches": 5,
         "rs_pct": 100, "volratio": 0.3, "updown": 2.0, "atr_to_pivot": 0.0, "stop_dist": 0.03, "liq_rank": 5}
    assert core.score_setup(m, CFG)["total"] == 100.0
    m2 = dict(m, rs_pct=80, depth=0.20, contr=0.60, volratio=0.75, touches=2)
    assert core.score_setup(m2, CFG) == core.score_setup(m2, CFG)
    assert 0 < core.score_setup(m2, CFG)["total"] < 100

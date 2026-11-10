"""The backtest's trade rules on hand-made bars, and a no-look-ahead check on the replay."""
import numpy as np
import pandas as pd
import pytest

from backtest import run as bt
from scanner import config as C, demo

CFG = C.load()
NAN = float("nan")


def bars(rows):
    """rows: (open, high, low, close, sma10) per session."""
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]


def test_stop_out_costs_one_r():
    o, h, l, c, s = bars([(10, 10.6, 9.9, 10.5, 9), (10.4, 10.5, 9.4, 9.6, 9)])
    r = bt.simulate(o, h, l, c, s, 0, entry=10.2, stop=9.7, intraday=True)
    assert r["r"] == pytest.approx(-1.0) and r["hold"] == 1 and not r["hit2r"]


def test_gap_through_the_stop_costs_more_than_one_r():
    o, h, l, c, s = bars([(10, 10.6, 9.9, 10.5, 9), (9.2, 9.4, 9.0, 9.3, 9)])
    r = bt.simulate(o, h, l, c, s, 0, entry=10.2, stop=9.7, intraday=True)
    assert r["r"] == pytest.approx((9.2 - 10.2) / 0.5)


def test_reversal_on_the_entry_day_is_a_loss_but_an_early_low_is_not():
    o, h, l, c, s = bars([(10, 10.6, 9.6, 9.9, 9)] + [(10, 10.2, 9.8, 10, 9)] * 3)
    assert bt.simulate(o, h, l, c, s, 0, 10.2, 9.7, True)["r"] == -1.0
    o, h, l, c, s = bars([(9.8, 10.9, 9.6, 10.8, 9), (10.8, 11, 10.7, 10.9, 9), (10.9, 11, 10.2, 10.3, 10.5)])
    r = bt.simulate(o, h, l, c, s, 0, 10.2, 9.7, True)
    assert r["ambiguous"] and r["r"] == pytest.approx(0.2) and r["r_pess"] == -1.0


def test_half_off_on_day_three_then_trail_on_the_ten_day():
    rows = [(10, 10.6, 10, 10.5, 9)]                                   # entry day at 10.2
    rows += [(10.5, 11, 10.4, 10.8, 9.5), (10.8, 11.5, 10.7, 11.2, 9.8)]
    rows += [(11.2, 12.4, 11.1, 12.2, 10.2)]                           # day 3: sell half at 12.2
    rows += [(12.2, 13, 12.1, 12.8, 10.8), (12.8, 12.9, 11.5, 11.6, 11.8)]   # close under the 10-day
    o, h, l, c, s = bars(rows)
    r = bt.simulate(o, h, l, c, s, 0, entry=10.2, stop=9.7, intraday=True)
    half, rest = (12.2 - 10.2) / 0.5, (11.6 - 10.2) / 0.5
    assert r["took_half"] and r["r"] == pytest.approx(0.5 * half + 0.5 * rest)
    assert r["hit2r"] and r["hold"] == 5 and r["complete"]


def test_after_the_partial_the_stop_is_the_entry_price():
    rows = [(10, 10.6, 10, 10.5, 9), (10.5, 11, 10.4, 10.8, 9), (10.8, 11.5, 10.7, 11.2, 9),
            (11.2, 11.4, 11, 11.2, 9), (11, 11.1, 10.0, 10.6, 9)]
    o, h, l, c, s = bars(rows)
    r = bt.simulate(o, h, l, c, s, 0, 10.2, 9.7, True)
    assert r["r"] == pytest.approx(0.5 * (11.2 - 10.2) / 0.5 + 0.0)


def test_entry_at_the_close_ignores_that_days_low_and_unfinished_trades_are_marked():
    rows = [(10, 10.6, 9.0, 10.5, 9), (10.5, 10.9, 10.4, 10.8, 9)]
    o, h, l, c, s = bars(rows)
    r = bt.simulate(o, h, l, c, s, 0, entry=10.5, stop=10.0, intraday=False)
    assert not r["complete"] and r["r"] == pytest.approx((10.8 - 10.5) / 0.5)
    assert bt.simulate(o, h, l, c, s, 0, entry=10.5, stop=10.5, intraday=False) is None


def test_replay_finds_the_built_in_setups_and_never_looks_ahead():
    data, meta = demo.make()
    rows, daily = bt.replay(data, meta, CFG)
    last = rows[rows["d"] == rows["d"].max()]
    ready = set(last[(last["kind"] == "base") & (last["status"] == "ready")]["ticker"])
    assert {"FLAG1", "FLAG2", "FLAG3"} <= ready
    assert "TRIG1" in set(last[last["kind"] == "fired"]["ticker"])
    assert {"EPIC1", "EPIC2"} <= set(last[last["kind"] == "ep"]["ticker"])
    assert len(daily) == demo.N - bt.WARMUP and set(daily["qqq_state"]) <= {"green", "amber", "red"}
    # the day before it fired, TRIG1 was ready, triggered the next session and was traded
    prev = rows[(rows["ticker"] == "TRIG1") & (rows["d"] == rows["d"].max() - 1)].iloc[0]
    assert prev["status"] == "ready" and prev["trig_next"] and prev["traded"]

    # Cut 25 sessions off the end: everything known at each earlier close must be unchanged
    cut = {f: df.iloc[:-25] for f, df in data.items()}
    early, _ = bt.replay(cut, meta, CFG)
    known = ["date", "ticker", "kind", "status", "score", "pivot", "adr", "rs", "dollar_vol",
             "base_count", *bt.FACTORS, "b_tight", "b_dist", "e_gap"]
    known = [k for k in known if k in early.columns]   # no gaps that early in the invented data
    assert len(early) > 500
    a = rows[rows["d"] <= early["d"].max()][known].reset_index(drop=True)
    b = early[known].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)

"""Fill, cost, and selection tests for the offline research study."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from research.calendar import cash_session, split_days
from research.combine import run_attempt, summarize_attempts
from research.costs import FEE_ROUND_TURN, contracts_for_stop
from research.market import Day, load_days
from research.simulate import simulate
from research.stats import empty_summary, train_ok, val_ok
from research.strategies import all_specs, neighbors_of, run_days, select

ROOT = Path(__file__).resolve().parents[1]


def _arr(*values):
    return np.asarray(values, dtype=float)


def test_market_round_turn_matches_the_published_example():
    # Buy the open at 100, pay 1 tick. Next bar trades through the stop at 99
    # with the open still above the stop, so the stop fills 1 tick through.
    result = simulate(
        _arr(100, 100),
        _arr(100.5, 100.5),
        _arr(99.75, 98),
        _arr(100, 99),
        side="long",
        entry_index=0,
        kind="market",
        level=None,
        stop=99,
        target=110,
        last_index=1,
        exit_on="close",
        qty=1,
    )
    assert result.filled
    assert result.reason == "stop"
    assert result.entry == pytest.approx(100.25)
    assert result.exit == pytest.approx(98.75)
    assert result.pnl == pytest.approx(-8.72)


def test_same_bar_stop_beats_the_target():
    result = simulate(
        _arr(100),
        _arr(110),
        _arr(90),
        _arr(100),
        side="long",
        entry_index=0,
        kind="market",
        level=None,
        stop=99,
        target=105,
        last_index=0,
        exit_on="close",
        qty=1,
    )
    assert result.reason == "stop"
    assert result.exit == pytest.approx(98.75)


def test_limit_fills_only_on_a_trade_through():
    missed = simulate(
        _arr(101),
        _arr(101.5),
        _arr(100),
        _arr(101),
        side="long",
        entry_index=0,
        kind="limit",
        level=100,
        stop=99,
        target=101,
        last_index=0,
        exit_on="close",
        qty=1,
    )
    assert missed.filled is False
    touched_through = simulate(
        _arr(100.5),
        _arr(101),
        _arr(99.75),
        _arr(100.5),
        side="long",
        entry_index=0,
        kind="limit",
        level=100,
        stop=99,
        target=102,
        last_index=0,
        exit_on="close",
        qty=1,
    )
    assert touched_through.filled
    assert touched_through.entry == pytest.approx(100)
    better = simulate(
        _arr(99),
        _arr(100),
        _arr(98),
        _arr(99.5),
        side="long",
        entry_index=0,
        kind="limit",
        level=100,
        stop=97,
        target=101,
        last_index=0,
        exit_on="close",
        qty=1,
    )
    assert better.entry == pytest.approx(99)


def test_buy_stop_gaps_and_pays_slippage():
    result = simulate(
        _arr(102),
        _arr(103),
        _arr(101),
        _arr(102.5),
        side="long",
        entry_index=0,
        kind="stop",
        level=101,
        stop=100,
        target=110,
        last_index=0,
        exit_on="close",
        qty=1,
    )
    assert result.entry == pytest.approx(102.25)


def test_short_stop_pays_slippage_on_the_cover():
    result = simulate(
        _arr(100, 100),
        _arr(100.25, 102),
        _arr(99.5, 99),
        _arr(100, 101),
        side="short",
        entry_index=0,
        kind="market",
        level=None,
        stop=101,
        target=90,
        last_index=1,
        exit_on="close",
        qty=1,
    )
    assert result.entry == pytest.approx(99.75)
    assert result.reason == "stop"
    assert result.exit == pytest.approx(101.25)
    assert result.pnl == pytest.approx(-8.72)


def test_fee_scales_with_quantity_and_time_exit_slips():
    kwargs = dict(
        opens=_arr(100, 100),
        highs=_arr(100.5, 100.5),
        lows=_arr(99.5, 99.5),
        closes=_arr(100, 100),
        side="long",
        entry_index=0,
        kind="market",
        level=None,
        stop=None,
        target=None,
        last_index=1,
        exit_on="open",
    )
    one = simulate(qty=1, **kwargs)
    two = simulate(qty=2, **kwargs)
    assert one.exit == pytest.approx(99.75)
    assert one.pnl == pytest.approx(-3.72)
    assert two.pnl == pytest.approx(-7.44)


def test_close_flatten_uses_the_close_not_the_next_bar():
    result = simulate(
        _arr(100),
        _arr(101),
        _arr(99.5),
        _arr(101),
        side="long",
        entry_index=0,
        kind="market",
        level=None,
        stop=None,
        target=None,
        last_index=0,
        exit_on="close",
        qty=1,
    )
    assert result.reason == "time"
    assert result.exit == pytest.approx(100.75)
    assert result.pnl == pytest.approx(1.28)


def test_research_size_uses_both_slippage_sides_and_the_real_fee():
    assert FEE_ROUND_TURN == pytest.approx(1.22)
    # 0.50 points * $5 + 2 ticks * $1.25 + $1.22 = $6.22; 200 / 6.22 = 32.
    assert contracts_for_stop(0.5) == 32
    assert contracts_for_stop(100) == 0
    assert contracts_for_stop(0.01, risk=100_000) == 50
    assert contracts_for_stop(0.5, slip_ticks_round_turn=1) == 40


def test_live_fee_and_retest_are_unchanged():
    text = (ROOT / "config" / "settings.yaml").read_text()
    assert "round_turn_fee_per_contract: 1.40" in text
    assert "retest_tolerance_ticks: 2" in text


def test_cash_session_rolls_the_weekend_into_monday():
    assert cash_session(2019, 5, 10, 17 * 60) == date(2019, 5, 13)
    assert cash_session(2019, 5, 12, 17 * 60) == date(2019, 5, 13)
    assert cash_session(2019, 5, 13, 8 * 60 + 30) == date(2019, 5, 13)
    assert cash_session(2019, 5, 10, 15 * 60) == date(2019, 5, 10)
    assert date(2019, 5, 10).weekday() == 4


def test_load_keeps_friday_and_sunday_overnight_in_order_and_drops_roll_levels(tmp_path):
    path = tmp_path / "mes.csv"
    path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume,contract",
                "2019-05-10T17:00:00-05:00,100,101,99,100,10,MESM9:1",
                "2019-05-12T17:00:00-05:00,102,103,101,102,10,MESM9:1",
                "2019-05-13T08:30:00-05:00,104,105,103,104,10,MESM9:1",
                "2019-05-13T15:00:00-05:00,106,107,105,106,10,MESM9:1",
                "2019-05-14T08:30:00-05:00,110,111,109,110,10,MESM9:1",
                "2019-05-14T10:00:00-05:00,200,201,199,200,10,MESU9:2",
                "2019-05-15T08:30:00-05:00,210,211,209,210,10,MESU9:2",
                "2019-05-15T15:00:00-05:00,212,213,211,212,10,MESU9:2",
            ]
        )
        + "\n"
    )
    days, _summary = load_days([str(path)])
    assert [day.session for day in days] == [date(2019, 5, 13), date(2019, 5, 14), date(2019, 5, 15)]
    monday = days[0]
    assert list(monday.o) == pytest.approx([100, 102, 104, 106])
    assert monday.onh == pytest.approx(103)
    assert monday.onl == pytest.approx(99)
    tuesday = days[1]
    assert len(tuesday.o) == 1
    assert tuesday.name.startswith("MESM9")
    assert tuesday.gap == pytest.approx(6)
    assert days[2].gap != days[2].gap
    assert days[2].name.startswith("MESU9")


def test_duplicate_minute_keeps_the_later_row(tmp_path):
    path = tmp_path / "dup.csv"
    path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume,contract",
                "2019-05-13T08:30:00-05:00,100,101,99,100,10,MESM9:1",
                "2019-05-13T08:30:00-05:00,100,101,99,999,11,MESM9:1",
                "2019-05-13T15:00:00-05:00,100,101,99,100,10,MESM9:1",
            ]
        )
        + "\n"
    )
    days, _summary = load_days([str(path)])
    assert len(days) == 1
    assert float(days[0].c[0]) == pytest.approx(999)


def _session_day(closes, opens=None, highs=None, lows=None, or_high=None, or_low=None):
    n = len(closes)
    minutes = np.asarray([8 * 60 + 30 + i for i in range(n)], dtype=np.int16)
    o = np.asarray(opens if opens is not None else [100.0] * n, dtype=float)
    h = np.asarray(highs if highs is not None else [100.5] * n, dtype=float)
    l = np.asarray(lows if lows is not None else [99.5] * n, dtype=float)
    c = np.asarray(closes, dtype=float)
    ends = np.arange(4, n, 5)
    if len(ends) == 0 or int(ends[-1]) != n - 1:
        ends = np.unique(np.append(ends, n - 1))
    nan = float("nan")
    return Day(
        session=date(2022, 1, 3),
        minute=minutes,
        o=o,
        h=h,
        l=l,
        c=c,
        v=np.ones(n),
        vwap=np.full(n, 100.0),
        atr=np.full(n, 1.0),
        ema=np.full(n, 100.0),
        five_end=ends.astype(np.int64),
        or_high={5: nan, 15: nan, 30: nan, **(or_high or {})},
        or_low={5: nan, 15: nan, 30: nan, **(or_low or {})},
        rth_i=0,
        flat_i=n - 1,
        exit_on="close",
        pdh=nan,
        pdl=nan,
        pdc=nan,
        onh=nan,
        onl=nan,
        gap=nan,
        rth_open=float(o[0]),
        contract=1,
        name="TEST",
    )


def test_orb_enters_on_the_bar_after_the_break_and_not_during_the_range():
    n = 40
    closes = [100.0] * n
    closes[14] = 110.0  # 08:44, still inside the 15-minute range window
    closes[24] = 102.0  # 08:54, first close outside a completed bucket after 08:45
    opens = [100.0] * n
    opens[25] = 102.0
    day = _session_day(closes, opens=opens, or_high={15: 101.0}, or_low={15: 99.0})
    spec = next(item for item in all_specs() if item.name == "orb_15_atr_1R")
    trades = run_days([day], spec)
    assert len(trades) == 1
    assert trades[0].entry == pytest.approx(102.25)

    closes[24] = 101.0
    flat = _session_day(closes, opens=opens, or_high={15: 101.0}, or_low={15: 99.0})
    assert run_days([flat], spec) == []


def test_grid_is_the_predeclared_66_and_neighbors_are_one_step():
    specs = all_specs()
    assert len(specs) == 66
    assert sum(item.eligible for item in specs) == 63
    assert len({item.name for item in specs}) == 66
    target = next(item for item in specs if item.name == "orb_15_mid_1R")
    names = {item.name for item in neighbors_of(target, specs)}
    assert names == {
        "orb_5_mid_1R",
        "orb_30_mid_1R",
        "orb_15_opposite_1R",
        "orb_15_atr_1R",
        "orb_15_mid_2R",
    }


def _summary(trades, expectancy, win_rate=0.5):
    summary = empty_summary()
    summary.update(
        {
            "trades": trades,
            "wins": int(round(win_rate * trades)),
            "win_rate": win_rate,
            "expectancy": expectancy,
            "net": expectancy * trades,
        }
    )
    return summary


def test_validation_must_agree_with_train():
    train = _summary(100, 10)
    assert train_ok(train)
    assert val_ok(train, _summary(40, 8, 0.55))
    assert not val_ok(train, _summary(40, -1))
    assert not val_ok(train, _summary(40, 1))  # under 25% of train
    assert not val_ok(train, _summary(40, 8, 0.9))  # win rate drifted more than 15 points
    assert not train_ok(_summary(99, 10))


def test_neighbor_sensitivity_and_select_ignore_holdout():
    specs = all_specs()
    name = "orb_15_mid_1R"
    neigh = {item.name for item in neighbors_of(next(item for item in specs if item.name == name), specs)}
    train = {item.name: _summary(0, 0) for item in specs}
    val = {item.name: _summary(0, 0) for item in specs}
    train[name] = _summary(120, 10)
    val[name] = _summary(50, 9)
    for other in neigh:
        train[other] = _summary(10, 1)  # too few train trades to qualify itself
        val[other] = _summary(40, 4)
    daily = {item.name: np.ones(5) for item in specs}
    assert [item.name for item in select(specs, train, val, daily)] == [name]

    bad = list(neigh)[:3]
    for other in bad:
        val[other] = _summary(40, -2)
    assert select(specs, train, val, daily) == []


def test_splits_keep_the_holdout_separate():
    class Stub:
        def __init__(self, session):
            self.session = session

    train, validation, holdout = split_days(
        [Stub(date(2023, 12, 29)), Stub(date(2024, 6, 3)), Stub(date(2025, 7, 1))]
    )
    assert [item.session for item in train] == [date(2023, 12, 29)]
    assert [item.session for item in validation] == [date(2024, 6, 3)]
    assert [item.session for item in holdout] == [date(2025, 7, 1)]


def _rows(start, pnls, worsts):
    rows = []
    for offset, (pnl, worst) in enumerate(zip(pnls, worsts)):
        rows.append({"session": start + timedelta(days=offset), "pnl": pnl, "worst": worst})
    return rows


def test_combine_pass_fail_and_trailing_floor():
    start = date(2024, 1, 2)
    passed = run_attempt(_rows(start, [1000, 1000, 1000], [-10, -10, -10]), start, start + timedelta(days=10))
    assert passed["status"] == "pass"
    assert passed["days"] == 3

    blocked = run_attempt(_rows(start, [3000], [-10]), start, start)
    assert blocked["status"] == "censored"
    assert blocked["consistency_blocked"] is True

    both = run_attempt(_rows(start, [-2000], [-2000]), start, start)
    assert both["status"] == "fail"
    assert both["reason"] == "dll+mll"

    # Give back open profit until a loss under $1,000 still touches the trailed floor.
    trailed = run_attempt(
        _rows(start, [1500, -600, -800, -600], [-10, -600, -800, -600]),
        start,
        start + timedelta(days=10),
    )
    assert trailed["status"] == "fail"
    assert trailed["reason"] == "mll"

    summary = summarize_attempts(_rows(start, [1000, 1000, 1000], [-10, -10, -10]), start + timedelta(days=10))
    assert summary["passes"] == 1
    assert summary["pass_rate_all"] == pytest.approx(1)

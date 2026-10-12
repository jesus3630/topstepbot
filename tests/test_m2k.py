"""M2K economics, the lagged yield rule, and the Sunday gap direction."""

from datetime import date

import numpy as np
import pytest

from research.costs import M2K, MES, contracts_for_stop
from research.market import Day
from research.predictors import overnight_return, prior_yield_change, run_m2k_overnight
from research.report import _cost_paragraph
from research.strategies import grid_text
from research.sunday import Episode, run_rule


def _cash_day(session, pdc, rth_open, later):
    minutes = np.asarray([8 * 60 + 30, 9 * 60 + 30, 15 * 60], dtype=np.int16)
    prices = np.asarray([rth_open, later, later], dtype=float)
    nan = float("nan")
    return Day(
        session=session,
        minute=minutes,
        o=prices.copy(),
        h=prices + 1,
        l=prices - 1,
        c=prices.copy(),
        v=np.ones(3),
        vwap=prices.copy(),
        atr=np.full(3, nan),
        ema=np.full(3, nan),
        five_end=np.zeros(3, dtype=np.int64),
        or_high={},
        or_low={},
        rth_i=0,
        flat_i=2,
        exit_on="close",
        pdh=pdc,
        pdl=pdc,
        pdc=pdc,
        onh=nan,
        onl=nan,
        gap=rth_open - pdc,
        rth_open=rth_open,
        contract=1,
        name="M",
    )


def test_m2k_tick_is_a_dime_and_the_published_fee_is_the_total():
    assert M2K.tick == pytest.approx(0.10)
    assert M2K.point_value == pytest.approx(5.0)
    assert M2K.tick_value == pytest.approx(0.50)
    assert M2K.fee_round_turn == pytest.approx(1.22)
    # 3-point stop: 3*5 + 2*0.50 + 1.22 = 17.22. floor(200/17.22) = 11.
    assert contracts_for_stop(3.0, fee=M2K.fee_round_turn, tick=M2K.tick, point_value=M2K.point_value) == 11
    text = grid_text("M2K")
    assert "TopstepX M2K round turn is $1.22" in text
    assert "1 tick ($0.50)" in text
    assert "at most 50 M2K" in text
    assert "$2.22" in _cost_paragraph(M2K)
    assert "does not print a separate" in _cost_paragraph(M2K)


def test_yield_change_ignores_the_same_day_print():
    series = {date(2024, 1, 2): 4.00, date(2024, 1, 3): 4.10, date(2024, 1, 4): 3.50}
    # Session is Jan 4. The same-day 3.50 print is invisible. Change is 4.10 - 4.00.
    assert prior_yield_change(date(2024, 1, 4), series) == pytest.approx(0.10)
    # Jan 3 has only one earlier print, so there is no change yet.
    assert prior_yield_change(date(2024, 1, 3), series) is None


def test_m2k_overnight_outperformance_buys_mes():
    session = date(2024, 1, 3)
    mes = _cash_day(session, 5000.0, 5000.0, 5000.0)
    # M2K overnight is +1.5%, which clears the 15bp relative bar. The path is flat after the open.
    m2k = _cash_day(session, 2000.0, 2030.0, 2030.0)
    assert overnight_return(m2k) == pytest.approx(0.015)
    trades = run_m2k_overnight([mes], {session: m2k})
    assert len(trades) == 1
    assert trades[0].side == "long"
    assert trades[0].name == "x_m2k_on_mes"
    quiet = _cash_day(session, 2000.0, 2001.0, 2001.0)
    assert run_m2k_overnight([mes], {session: quiet}) == []


def test_sunday_gap_up_is_faded_and_a_roll_is_skipped():
    session = date(2024, 1, 8)  # Monday
    minutes = np.asarray([17 * 60, 17 * 60 + 1, 8 * 60 + 30], dtype=np.int16)
    # Gap up 10 points. Next bar and Monday open stay there, so the fade can be held.
    prices = np.asarray([110.0, 110.0, 110.0], dtype=float)
    episode = Episode(
        sunday=date(2024, 1, 7),
        trade_date=session,
        next_open=session,
        friday_close=100.0,
        friday_date=date(2024, 1, 5),
        contract="MESZ4",
        rolled=False,
        minute=minutes,
        o=prices.copy(),
        h=prices + 0.25,
        l=prices - 0.25,
        c=prices.copy(),
        v=np.ones(3),
    )
    trades = run_rule([episode], "sun_gap_fade", MES)
    assert len(trades) == 1
    assert trades[0].side == "short"
    rolled = Episode(
        sunday=episode.sunday,
        trade_date=session,
        next_open=session,
        friday_close=None,
        friday_date=episode.friday_date,
        contract="MESH5",
        rolled=True,
        minute=minutes,
        o=prices.copy(),
        h=prices + 0.25,
        l=prices - 0.25,
        c=prices.copy(),
        v=np.ones(3),
    )
    assert rolled.gap is None
    assert run_rule([rolled], "sun_gap_fade", MES) == []


def test_live_settings_file_is_still_untouched():
    text = pytest.importorskip("pathlib").Path(__file__).resolve().parents[1].joinpath("config/settings.yaml").read_text()
    assert "round_turn_fee_per_contract: 1.40" in text

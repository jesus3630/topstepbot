"""Calendar tags and the four cross-market rules. No full history file."""

from datetime import date

import numpy as np
import pytest

from research.costs import MES, MNQ
from research.cross import _clock_bucket, _ratio_trade, cross_candidate, first_hour_return, run_confirm
from research.events import (
    half_days,
    ism_dates,
    load_releases,
    nyse_holidays,
    opex_dates,
    red_days,
    release_session,
    tag_sessions,
)
from research.market import Day


def _day(session, closes, opens=None):
    n = len(closes)
    minutes = np.asarray([8 * 60 + 30 + i for i in range(n)], dtype=np.int16)
    o = np.asarray(opens if opens is not None else closes, dtype=float)
    c = np.asarray(closes, dtype=float)
    nan = float("nan")
    return Day(
        session=session,
        minute=minutes,
        o=o,
        h=np.maximum(o, c) + 0.25,
        l=np.minimum(o, c) - 0.25,
        c=c,
        v=np.ones(n),
        vwap=np.full(n, 100.0),
        atr=np.full(n, 1.0),
        ema=np.full(n, 100.0),
        five_end=np.asarray([n - 1], dtype=np.int64),
        or_high={5: nan, 15: nan, 30: nan},
        or_low={5: nan, 15: nan, 30: nan},
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


def test_official_release_dates_and_the_sunday_fomc_session():
    releases = load_releases()
    assert "2024-01-05" in releases["nfp"]
    assert "2024-01-11" in releases["cpi"]
    assert "2024-01-12" in releases["ppi"]
    assert "2024-01-31" in releases["fomc"]
    assert "2020-03-03" in releases["fomc"]
    assert "2020-03-15" in releases["fomc"]
    assert "2020-03-23" in releases["fomc_notation"]
    assert "2025-10-24" in releases["cpi"]
    assert release_session(date(2020, 3, 15), nyse_holidays(2020)) == date(2020, 3, 16)
    assert date(2024, 5, 27) in nyse_holidays(2024)
    assert date(2022, 6, 20) in nyse_holidays(2022)
    assert date(2020, 4, 10) in nyse_holidays(2020)
    assert date(2019, 11, 29) in half_days(2019)
    assert date(2021, 12, 24) not in half_days(2021)
    assert opex_dates(2024)[date(2024, 1, 19)] == "opex"
    assert opex_dates(2024)[date(2024, 3, 15)] == "quad"
    assert ism_dates(2024)[date(2024, 1, 2)] == "ism_mfg"
    assert ism_dates(2024)[date(2024, 1, 4)] == "ism_svc"
    tagged = tag_sessions([date(2024, 1, 5), date(2024, 1, 11), date(2024, 1, 16)])
    assert "nfp" in tagged[date(2024, 1, 5)]
    assert "red" in tagged[date(2024, 1, 5)]
    assert "cpi" in tagged[date(2024, 1, 11)]
    assert "red" not in tagged[date(2024, 1, 16)]
    assert date(2024, 1, 5) in red_days([date(2024, 1, 5), date(2024, 1, 16)])


def test_confirm_requires_the_same_15bp_sign_and_ratio_stays_flat_inside_the_band():
    session = date(2022, 1, 3)
    up = [100.0] * 70
    up[59] = 100.40
    opens = [100.0] * 70
    opens[60] = 100.40
    mes = _day(session, up, opens)
    mnq_up = [400.0] * 70
    mnq_up[59] = 401.60
    mnq_opens = [400.0] * 70
    mnq_opens[60] = 401.60
    mnq = _day(session, mnq_up, mnq_opens)
    assert first_hour_return(mes) == pytest.approx(0.004)
    trades = run_confirm([mes], {session: mnq}, MES, "x_confirm_mes")
    assert len(trades) == 1
    assert trades[0].name == "x_confirm_mes"
    down = [400.0] * 70
    down[59] = 398.0
    assert run_confirm([mes], {session: _day(session, down, mnq_opens)}, MES, "x_confirm_mes") == []

    flat_mes = _day(session, [100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 100.0, 100.0])
    # Rebuild clocks explicitly: 8:30, 9:30, 10:00, 15:00.
    flat_mes.minute = np.asarray([8 * 60 + 30, 9 * 60 + 30, 10 * 60, 15 * 60], dtype=np.int16)
    flat_mnq = _day(session, [400.0, 400.0, 400.0, 400.0])
    flat_mnq.minute = flat_mes.minute.copy()
    assert _ratio_trade(flat_mes, flat_mnq, 4.0) is None
    rich = _ratio_trade(flat_mes, flat_mnq, 3.90)
    assert rich is not None
    assert rich.name == "x_ratio_revert"
    assert rich.qty >= 2
    assert cross_candidate({"trades": 10, "expectancy": 1, "net": 10}, {"trades": 30, "expectancy": 1, "net": 10}) is False
    assert cross_candidate({"trades": 40, "expectancy": 2, "net": 80}, {"trades": 20, "expectancy": 1, "net": 20}) is True


def test_half_hour_bucket_keeps_the_open_and_the_last_half_hour():
    assert _clock_bucket(8 * 60 + 31) == 8 * 60 + 30
    assert _clock_bucket(8 * 60 + 59) == 8 * 60 + 30
    assert _clock_bucket(9 * 60) == 9 * 60
    assert _clock_bucket(14 * 60 + 30) == 14 * 60 + 30
    assert _clock_bucket(14 * 60 + 59) == 14 * 60 + 30


def test_live_settings_file_is_still_the_mes_placeholder():
    text = (pytest.importorskip("pathlib").Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text()
    assert "round_turn_fee_per_contract: 1.40" in text
    assert MNQ.fee_round_turn == pytest.approx(1.22)

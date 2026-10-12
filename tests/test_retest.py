from datetime import datetime
from zoneinfo import ZoneInfo

from topstepbot.indicators import is_retest_bar
from topstepbot.models import Bar, Side

TZ = ZoneInfo("America/Chicago")
TICK = 0.25
TOLERANCE = 2 * TICK


def _bar(low, high, close, open_=None):
    return Bar(
        time=datetime(2026, 1, 6, 8, 50, tzinfo=TZ),
        open=open_ if open_ is not None else close,
        high=high,
        low=low,
        close=close,
        volume=100,
    )


def test_long_retest_touches_within_two_ticks_and_closes_back_above():
    level = 5648.0
    bar = _bar(low=5648.0, high=5650.0, close=5649.0)
    assert is_retest_bar(bar, level, Side.LONG, TOLERANCE)


def test_long_retest_rejects_a_wick_that_is_too_deep():
    level = 5648.0
    bar = _bar(low=5646.0, high=5650.0, close=5649.0)
    assert not is_retest_bar(bar, level, Side.LONG, TOLERANCE)


def test_long_retest_rejects_a_close_back_through_the_level():
    level = 5648.0
    bar = _bar(low=5648.0, high=5649.0, close=5647.75)
    assert not is_retest_bar(bar, level, Side.LONG, TOLERANCE)


def test_short_retest_is_the_mirror():
    level = 5600.0
    bar = _bar(low=5598.0, high=5600.25, close=5599.0)
    assert is_retest_bar(bar, level, Side.SHORT, TOLERANCE)
    missed = _bar(low=5598.0, high=5602.0, close=5599.0)
    assert not is_retest_bar(missed, level, Side.SHORT, TOLERANCE)

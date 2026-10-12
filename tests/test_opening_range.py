from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from topstepbot.levels import opening_range_from_bars
from topstepbot.models import Bar

TZ = ZoneInfo("America/Chicago")


def _bar(hour, minute, high, low, close=None):
    return Bar(
        time=datetime(2026, 1, 6, hour, minute, tzinfo=TZ),
        open=low,
        high=high,
        low=low,
        close=close if close is not None else high,
        volume=10,
    )


def test_opening_range_uses_0830_through_0844_only():
    bars = [
        _bar(8, 29, 10, 9),
        _bar(8, 30, 12, 11),
        _bar(8, 44, 15, 13),
        _bar(8, 45, 20, 1),
    ]
    opening = opening_range_from_bars(
        bars,
        datetime(2026, 1, 6, tzinfo=TZ).date(),
        "America/Chicago",
        datetime.strptime("08:30", "%H:%M").time(),
        datetime.strptime("08:45", "%H:%M").time(),
    )
    assert opening is not None
    assert opening.high == 15
    assert opening.low == 11


def test_opening_range_empty_before_the_window():
    bars = [_bar(8, 0, 5, 4)]
    opening = opening_range_from_bars(
        bars,
        datetime(2026, 1, 6, tzinfo=TZ).date(),
        "America/Chicago",
        datetime.strptime("08:30", "%H:%M").time(),
        datetime.strptime("08:45", "%H:%M").time(),
    )
    assert opening is None


def test_opening_range_ignores_other_days():
    monday = _bar(8, 30, 99, 90)
    monday = Bar(
        time=datetime(2026, 1, 5, 8, 30, tzinfo=TZ),
        open=90,
        high=99,
        low=90,
        close=95,
        volume=1,
    )
    tuesday = _bar(8, 31, 12, 10)
    opening = opening_range_from_bars(
        [monday, tuesday],
        tuesday.time.date(),
        "America/Chicago",
        datetime.strptime("08:30", "%H:%M").time(),
        datetime.strptime("08:45", "%H:%M").time(),
    )
    assert opening.high == 12
    assert opening.low == 10

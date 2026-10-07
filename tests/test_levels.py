from datetime import datetime, time
from zoneinfo import ZoneInfo

from topstepbot.levels import floor_pivots, next_target, overnight_hl
from topstepbot.models import Bar, PriceLevel

TZ = ZoneInfo("America/Chicago")


def test_floor_pivots_match_the_classic_formulas():
    pivots = floor_pivots(high=30, low=20, close=25)
    assert pivots["pp"] == 25
    assert pivots["r1"] == 30
    assert pivots["s1"] == 20
    assert pivots["r2"] == 35
    assert pivots["s2"] == 15
    assert pivots["r3"] == 40
    assert pivots["s3"] == 10


def test_overnight_window_is_1700_to_0830():
    bars = [
        Bar(datetime(2026, 1, 5, 16, 59, tzinfo=TZ), 1, 2, 1, 1, 1),
        Bar(datetime(2026, 1, 5, 17, 0, tzinfo=TZ), 10, 12, 9, 11, 1),
        Bar(datetime(2026, 1, 6, 8, 29, tzinfo=TZ), 10, 14, 8, 13, 1),
        Bar(datetime(2026, 1, 6, 8, 30, tzinfo=TZ), 10, 99, 1, 50, 1),
    ]
    high, low = overnight_hl(
        bars,
        datetime(2026, 1, 6, tzinfo=TZ).date(),
        "America/Chicago",
        time(17, 0),
        time(8, 30),
    )
    assert high == 14
    assert low == 8


def test_next_target_requires_two_r_and_skips_nearer_levels():
    levels = [
        PriceLevel("near", 101),
        PriceLevel("far", 110),
        PriceLevel("behind", 90),
    ]
    # R = 2, so 2R needs 4 points of room. 101 is only 1 point away.
    target = next_target(levels, entry=100, stop=98, side_is_long=True, min_reward_risk=2)
    assert target == 110


def test_next_target_returns_none_when_nothing_is_far_enough():
    levels = [PriceLevel("near", 101)]
    assert next_target(levels, entry=100, stop=98, side_is_long=True, min_reward_risk=2) is None

"""Synthetic MES 1-minute bars so the backtest runs without an API key.

The path is invented. It is not a recording of the S&P. Monday and the overnight
session grind higher so the 21 EMA is already under price on Tuesday. Tuesday's
opening range, break, retest, and push to the next round number are drawn to
satisfy Setup A. Nothing here is a market forecast.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from topstepbot.models import Bar

TZ = ZoneInfo("America/Chicago")
TICK = 0.25


def build_synthetic_bars() -> list[Bar]:
    bars: list[Bar] = []
    price = 5590.0
    price = _drift(bars, datetime(2026, 1, 5, 8, 30, tzinfo=TZ), 390, price, step=0.04, volume=100)
    price = _drift(bars, datetime(2026, 1, 5, 17, 0, tzinfo=TZ), 930, price, step=0.04, volume=80)
    or_high = _tick(price + 1.0)
    or_low = _tick(price - 2.0)
    bars.extend(_opening_range(datetime(2026, 1, 6, 8, 30, tzinfo=TZ), price, or_low, or_high))
    bars.extend(_strong_break(datetime(2026, 1, 6, 8, 45, tzinfo=TZ), or_high))
    bars.extend(_retest(datetime(2026, 1, 6, 8, 50, tzinfo=TZ), or_high))
    # Reach the next round number inside the 45-minute time stop, then hold it.
    bars.extend(_rally(datetime(2026, 1, 6, 8, 55, tzinfo=TZ), or_high + 1.0, or_high + 36.0, 25))
    bars.extend(_hold(datetime(2026, 1, 6, 9, 20, tzinfo=TZ), 70, or_high + 36.0))
    return bars


def _drift(bars: list[Bar], start: datetime, minutes: int, price: float, step: float, volume: float) -> float:
    cursor = price
    for i in range(minutes):
        nxt = cursor + step
        bars.append(
            Bar(
                time=start + timedelta(minutes=i),
                open=_tick(cursor),
                high=_tick(max(cursor, nxt) + 2.0),
                low=_tick(min(cursor, nxt) - 2.0),
                close=_tick(nxt),
                volume=volume,
            )
        )
        cursor = nxt
    return cursor


def _opening_range(start: datetime, price: float, low: float, high: float) -> list[Bar]:
    """15 one-minute bars inside the opening range, with the high and low printed once."""
    bars = []
    for i in range(15):
        if i % 2 == 0:
            open_, close = price - 0.25, price + 0.25
        else:
            open_, close = price + 0.25, price - 0.25
        bar_high = high if i == 4 else max(open_, close) + 0.25
        bar_low = low if i == 7 else min(open_, close) - 0.25
        bars.append(
            Bar(
                time=start + timedelta(minutes=i),
                open=_tick(open_),
                high=_tick(max(bar_high, open_, close)),
                low=_tick(min(bar_low, open_, close)),
                close=_tick(close),
                volume=90,
            )
        )
    return bars


def _strong_break(start: datetime, level: float) -> list[Bar]:
    """Five minutes that aggregate into one strong close beyond the level."""
    open_ = level - 0.5
    close = level + 3.0
    low = level - 1.0
    bars = []
    for i in range(5):
        o = open_ + (close - open_) * (i / 5)
        c = open_ + (close - open_) * ((i + 1) / 5)
        bars.append(
            Bar(
                time=start + timedelta(minutes=i),
                open=_tick(o),
                high=_tick(max(o, c) + (0.5 if i == 4 else 0.25)),
                low=_tick(low if i == 0 else min(o, c) - 0.25),
                close=_tick(c),
                volume=500,
            )
        )
    return bars


def _retest(start: datetime, level: float) -> list[Bar]:
    """Tag the broken level and close back above it. The high stays tight so R is small."""
    # Aggregated bar: low == level, high == level+1, close == level+0.75.
    path = [
        (level + 2.5, level + 2.5, level + 1.5, level + 1.75),
        (level + 1.75, level + 1.75, level, level + 0.5),
        (level + 0.5, level + 1.0, level + 0.25, level + 0.75),
        (level + 0.75, level + 1.0, level + 0.5, level + 0.75),
        (level + 0.75, level + 1.0, level + 0.5, level + 0.75),
    ]
    bars = []
    for i, (o, h, l, c) in enumerate(path):
        bars.append(
            Bar(
                time=start + timedelta(minutes=i),
                open=_tick(o),
                high=_tick(max(o, h, c)),
                low=_tick(min(o, l, c)),
                close=_tick(c),
                volume=140,
            )
        )
    return bars


def _rally(start: datetime, price: float, end: float, minutes: int) -> list[Bar]:
    """Climb without dipping, so the buy stop fills and the target can be reached."""
    bars = []
    cursor = price
    step = (end - price) / minutes
    for i in range(minutes):
        nxt = cursor + step
        bars.append(
            Bar(
                time=start + timedelta(minutes=i),
                open=_tick(cursor),
                high=_tick(max(cursor, nxt) + 0.25),
                low=_tick(min(cursor, nxt) - 0.25),
                close=_tick(nxt),
                volume=110,
            )
        )
        cursor = nxt
    return bars


def _hold(start: datetime, minutes: int, price: float) -> list[Bar]:
    bars = []
    for i in range(minutes):
        bars.append(
            Bar(
                time=start + timedelta(minutes=i),
                open=_tick(price),
                high=_tick(price + 0.5),
                low=_tick(price - 0.5),
                close=_tick(price if i % 2 == 0 else price + 0.25),
                volume=60,
            )
        )
    return bars


def _tick(price: float) -> float:
    return round(round(price / TICK) * TICK, 2)

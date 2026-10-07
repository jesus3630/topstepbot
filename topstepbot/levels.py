"""Key-level map and the 15-minute opening range.

Prior-day high/low/close use the RTH window in config (default 08:30-15:00 CT).
Overnight high/low use the Globex anchor (default 17:00 CT) through the cash open.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from topstepbot.models import Bar, OpeningRange, PriceLevel
from topstepbot.timeutil import as_chicago


def floor_pivots(high: float, low: float, close: float) -> dict[str, float]:
    pp = (high + low + close) / 3.0
    return {
        "pp": pp,
        "r1": 2 * pp - low,
        "s1": 2 * pp - high,
        "r2": pp + (high - low),
        "s2": pp - (high - low),
        "r3": high + 2 * (pp - low),
        "s3": low - 2 * (high - pp),
    }


def _local_minutes(moment: datetime, tz_name: str) -> tuple[date, int]:
    local = as_chicago(moment, tz_name)
    return local.date(), local.hour * 60 + local.minute


def bars_between(
    bars: list[Bar],
    tz_name: str,
    start: datetime,
    end: datetime,
) -> list[Bar]:
    """Bars whose open time is >= start and < end."""
    return [bar for bar in bars if start <= bar.time < end]


def opening_range_from_bars(
    bars: list[Bar],
    session: date,
    tz_name: str,
    rth_open: time,
    range_end: time,
) -> OpeningRange | None:
    """High and low of [rth_open, range_end) on `session`.

    With the default clocks that is 08:30:00 through 08:44:59 CT.
    """
    start_m = rth_open.hour * 60 + rth_open.minute
    end_m = range_end.hour * 60 + range_end.minute
    chosen = []
    for bar in bars:
        day, minutes = _local_minutes(bar.time, tz_name)
        if day == session and start_m <= minutes < end_m:
            chosen.append(bar)
    if not chosen:
        return None
    return OpeningRange(
        session_date=session,
        high=max(bar.high for bar in chosen),
        low=min(bar.low for bar in chosen),
    )


def prior_rth_hlc(
    bars: list[Bar],
    session: date,
    tz_name: str,
    rth_open: time,
    prior_rth_end: time,
) -> tuple[float, float, float] | None:
    """Previous weekday's RTH high, low, and last price before prior_rth_end."""
    previous = session - timedelta(days=1)
    # Walk back over weekends. Sunday-open futures still have a Friday RTH.
    for _ in range(4):
        if previous.weekday() < 5:
            break
        previous -= timedelta(days=1)
    start_m = rth_open.hour * 60 + rth_open.minute
    end_m = prior_rth_end.hour * 60 + prior_rth_end.minute
    chosen = []
    for bar in bars:
        day, minutes = _local_minutes(bar.time, tz_name)
        if day == previous and start_m <= minutes < end_m:
            chosen.append(bar)
    if not chosen:
        return None
    chosen.sort(key=lambda bar: bar.time)
    return (
        max(bar.high for bar in chosen),
        min(bar.low for bar in chosen),
        chosen[-1].close,
    )


def overnight_hl(
    bars: list[Bar],
    session: date,
    tz_name: str,
    vwap_anchor: time,
    rth_open: time,
) -> tuple[float, float] | None:
    """High/low from the prior day's anchor (17:00 CT) until today's cash open."""
    from zoneinfo import ZoneInfo

    anchor_day = session - timedelta(days=1)
    tz = ZoneInfo(tz_name)
    start = datetime(
        anchor_day.year,
        anchor_day.month,
        anchor_day.day,
        vwap_anchor.hour,
        vwap_anchor.minute,
        tzinfo=tz,
    )
    end = datetime(
        session.year,
        session.month,
        session.day,
        rth_open.hour,
        rth_open.minute,
        tzinfo=tz,
    )
    chosen = [bar for bar in bars if start <= bar.time < end]
    if not chosen:
        return None
    return max(bar.high for bar in chosen), min(bar.low for bar in chosen)


def london_hl(
    bars: list[Bar],
    session: date,
    tz_name: str,
    rth_open: time,
    london_start: time = time(2, 0),
) -> tuple[float, float] | None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    start = datetime(
        session.year, session.month, session.day, london_start.hour, london_start.minute, tzinfo=tz
    )
    end = datetime(
        session.year, session.month, session.day, rth_open.hour, rth_open.minute, tzinfo=tz
    )
    chosen = [bar for bar in bars if start <= bar.time < end]
    if not chosen:
        return None
    return max(bar.high for bar in chosen), min(bar.low for bar in chosen)


def round_numbers(price: float, step: float, span: float) -> list[float]:
    if step <= 0:
        return []
    low = price - span
    high = price + span
    first = (int(low // step) + 1) * step
    values = []
    cursor = first
    while cursor < high + 1e-9:
        if cursor > low:
            values.append(round(cursor, 10))
        cursor += step
    return values


def build_level_map(
    *,
    prior: tuple[float, float, float] | None,
    overnight: tuple[float, float] | None,
    london: tuple[float, float] | None,
    opening: OpeningRange | None,
    vwap: float | None,
    round_step: float,
    near_price: float,
) -> list[PriceLevel]:
    levels: list[PriceLevel] = []
    if prior is not None:
        high, low, close = prior
        levels.append(PriceLevel("prior_high", high))
        levels.append(PriceLevel("prior_low", low))
        levels.append(PriceLevel("prior_close", close))
        for name, price in floor_pivots(high, low, close).items():
            levels.append(PriceLevel(name, price))
    if overnight is not None:
        levels.append(PriceLevel("overnight_high", overnight[0]))
        levels.append(PriceLevel("overnight_low", overnight[1]))
    if london is not None:
        levels.append(PriceLevel("london_high", london[0]))
        levels.append(PriceLevel("london_low", london[1]))
    if opening is not None:
        levels.append(PriceLevel("or_high", opening.high))
        levels.append(PriceLevel("or_low", opening.low))
    if vwap is not None:
        levels.append(PriceLevel("vwap", vwap))
    for price in round_numbers(near_price, round_step, span=100):
        levels.append(PriceLevel(f"round_{price:.2f}", price))
    return levels


def next_target(
    levels: list[PriceLevel],
    entry: float,
    stop: float,
    side_is_long: bool,
    min_reward_risk: float,
    exclude_prices: set[float] | None = None,
) -> float | None:
    """Nearest key level at least min_reward_risk * R beyond the entry."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    need = min_reward_risk * risk
    banned = exclude_prices or set()
    if side_is_long:
        candidates = sorted(
            lvl.price
            for lvl in levels
            if lvl.price > entry + 1e-9 and all(abs(lvl.price - b) > 1e-6 for b in banned)
        )
        for price in candidates:
            if price - entry >= need - 1e-6:
                return price
        return None
    candidates = sorted(
        (
            lvl.price
            for lvl in levels
            if lvl.price < entry - 1e-9 and all(abs(lvl.price - b) > 1e-6 for b in banned)
        ),
        reverse=True,
    )
    for price in candidates:
        if entry - price >= need - 1e-6:
            return price
    return None


def level_by_name(levels: list[PriceLevel], name: str) -> float | None:
    for lvl in levels:
        if lvl.name == name:
            return lvl.price
    return None

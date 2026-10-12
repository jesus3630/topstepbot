"""Central-time helpers. The trading clock is America/Chicago all year."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from topstepbot.config import SessionConfig

CHICAGO = ZoneInfo("America/Chicago")


def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def as_chicago(moment: datetime, tz_name: str = "America/Chicago") -> datetime:
    if moment.tzinfo is None:
        raise ValueError("refusing a naive timestamp; pass a timezone")
    return moment.astimezone(ZoneInfo(tz_name))


def minutes_of(clock: time) -> int:
    return clock.hour * 60 + clock.minute


def at_clock(day: date, clock: time, tz_name: str) -> datetime:
    return datetime(day.year, day.month, day.day, clock.hour, clock.minute, tzinfo=ZoneInfo(tz_name))


def session_date(moment: datetime, session: SessionConfig) -> date:
    """The cash-session date this timestamp belongs to.

    Bars from the 17:00 CT Globex open belong to the next cash session,
    through the next day's hard flat.
    """
    local = as_chicago(moment, session.timezone)
    if local.time() >= session.vwap_anchor:
        return (local.date() + timedelta(days=1))
    return local.date()


def clock_reached(moment: datetime, clock: time, tz_name: str) -> bool:
    local = as_chicago(moment, tz_name)
    return (local.hour, local.minute, local.second) >= (clock.hour, clock.minute, 0)

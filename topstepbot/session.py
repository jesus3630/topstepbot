"""Session clock. New entries only inside the entry window; flat by 10:30 CT."""

from __future__ import annotations

from datetime import datetime

from topstepbot.config import SessionConfig
from topstepbot.models import Phase
from topstepbot.timeutil import as_chicago, minutes_of


def phase_at(moment: datetime, session: SessionConfig) -> Phase:
    """Clock phase for a timestamp.

    The Globex evening (at/after the VWAP anchor, default 17:00 CT) belongs to
    the next cash session and is not a flatten signal. Flatten runs from
    `flatten_time` until that anchor, which covers Topstep's 15:10 CT cutoff
    as long as flatten_time is at or before hard_flat_time.
    """
    local = as_chicago(moment, session.timezone)
    minute = local.hour * 60 + local.minute
    open_m = minutes_of(session.rth_open)
    range_end = minutes_of(session.opening_range_end)
    entry_start = minutes_of(session.entry_start)
    entry_end = minutes_of(session.entry_end)
    flatten = minutes_of(session.flatten_time)
    anchor = minutes_of(session.vwap_anchor)

    if minute >= anchor:
        return Phase.BEFORE
    if minute >= flatten:
        return Phase.FLAT
    if minute < open_m:
        return Phase.BEFORE
    if minute < max(range_end, entry_start):
        return Phase.OPENING_RANGE
    if minute <= entry_end:
        return Phase.ENTRY
    return Phase.MANAGE


def entries_allowed(moment: datetime, session: SessionConfig) -> bool:
    return phase_at(moment, session) is Phase.ENTRY


def must_flatten(moment: datetime, session: SessionConfig) -> bool:
    return phase_at(moment, session) is Phase.FLAT


def setup_b_allowed(moment: datetime, session: SessionConfig) -> bool:
    if not entries_allowed(moment, session):
        return False
    local = as_chicago(moment, session.timezone)
    minute = local.hour * 60 + local.minute
    return minute <= minutes_of(session.setup_b_entry_end)

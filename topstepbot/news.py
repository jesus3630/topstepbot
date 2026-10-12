"""Blackout dates and the manual no-trade switch.

There is no economic-calendar download. The trader types the dates.
"""

from __future__ import annotations

from datetime import datetime

from topstepbot.config import NewsConfig
from topstepbot.timeutil import as_chicago, minutes_of


def block_reason(moment: datetime, news: NewsConfig, tz_name: str) -> str | None:
    if news.no_trade_today:
        return "no_trade_today switch is on"
    local = as_chicago(moment, tz_name)
    iso = local.date().isoformat()
    if iso in news.blackout_dates:
        return f"blackout date {iso}"
    minute = local.hour * 60 + local.minute
    for window in news.intraday_blackouts:
        if window.on_date != iso:
            continue
        if minutes_of(window.start) <= minute < minutes_of(window.end):
            return f"intraday blackout {iso} {window.start.strftime('%H:%M')}-{window.end.strftime('%H:%M')}"
    return None

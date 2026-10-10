"""Event-day tags for the research note. Not used by the live bot.

NFP, CPI, and PPI dates come from the BLS annual release schedules
(2019–2026), saved from the Internet Archive copies of
https://www.bls.gov/schedule/YEAR/ because bls.gov returned 403 here.
Only rows timed 8:30 AM ET whose names are "Employment Situation for",
"Consumer Price Index for", or "Producer Price Index for" are kept.
"Employment Situation of Veterans" is not NFP.

FOMC dates are the primary statement links (monetaryYYYYMMDDa.htm) on
https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm and the
2019 and 2020 historical pages. A statement whose heading says
"notation vote" is tagged separately and is not part of the red-day set.

The red-day set was written down before any MNQ or event result was
scored: NFP, CPI, and FOMC decision days. PPI, ISM, and the notation
votes are reported. They are not added to the skip list after the fact.

ISM Manufacturing is the first NYSE business day of the month and ISM
Services is the third, both 10:00 AM ET (9:00 CT). That is ISM's standing
rule. A one-off ISM shift is not in this file.

Half days follow the NYSE early-close pattern (1:00 PM ET): the Friday
after Thanksgiving, July 3 when it is the weekday before a weekday July 4,
and December 24 when it is a weekday and not the observed Christmas close.
The archived NYSE hours page states that pattern for the dates it lists.
A one-off early close outside the pattern would be missed.

OPEX is the third Friday, moved to Thursday when that Friday is a full
NYSE close. Quad witching is the March, June, September, and December
OPEX. Roll week is the Monday–Friday cash week of that quarterly OPEX.
Month-end is the last loaded cash session of each calendar month.
"""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data"
_RELEASES = _DATA / "releases.json"
_VIX = _DATA / "vix_close.csv"

# Prior-close VIX buckets. Round numbers, not fit to the trades.
VIX_HIGH = 20.0
VIX_LOW = 15.0


def _easter(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def nyse_holidays(year: int) -> set[date]:
    """Full NYSE closes. Juneteenth is included from 2022, when the exchange adopted it."""
    days = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter(year) - timedelta(days=2),
        _nth_weekday(year, 5, 0, 1) + timedelta(days=21),  # last Monday is the 4th or 5th
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    # Last Monday in May: the helper above starts at the first Monday and adds 21 days,
    # which is the fourth Monday. May can have a fifth.
    memorial = _nth_weekday(year, 5, 0, 4)
    if memorial + timedelta(days=7) <= date(year, 5, 31):
        memorial = memorial + timedelta(days=7)
    days.discard(_nth_weekday(year, 5, 0, 1) + timedelta(days=21))
    days.add(memorial)
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))
    # New Year's observed on Friday of the prior year is not this year's set.
    days = {day for day in days if day.year == year}
    return days


def half_days(year: int) -> set[date]:
    holidays = nyse_holidays(year)
    out = set()
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    black_friday = thanksgiving + timedelta(days=1)
    if black_friday not in holidays and black_friday.weekday() < 5:
        out.add(black_friday)
    july4 = date(year, 7, 4)
    if july4.weekday() in (1, 2, 3, 4):
        july3 = date(year, 7, 3)
        if july3 not in holidays and july3.weekday() < 5:
            out.add(july3)
    christmas = _observed(date(year, 12, 25))
    eve = date(year, 12, 24)
    if eve.weekday() < 5 and eve not in holidays and eve != christmas:
        out.add(eve)
    return out


def _third_friday(year: int, month: int) -> date:
    return _nth_weekday(year, month, 4, 3)


def opex_dates(year: int) -> dict[date, str]:
    """Map the expiration session to 'quad' or 'opex'. Quad is also an OPEX."""
    holidays = nyse_holidays(year)
    out = {}
    for month in range(1, 13):
        day = _third_friday(year, month)
        if day in holidays or day.weekday() >= 5:
            day = day - timedelta(days=1)
            while day in holidays or day.weekday() >= 5:
                day = day - timedelta(days=1)
        out[day] = "quad" if month in (3, 6, 9, 12) else "opex"
    return out


def roll_week_dates(year: int) -> set[date]:
    """Monday through Friday of the week that contains the quarterly expiration."""
    holidays = nyse_holidays(year)
    out = set()
    for month in (3, 6, 9, 12):
        expiry = _third_friday(year, month)
        if expiry in holidays or expiry.weekday() >= 5:
            expiry = expiry - timedelta(days=1)
        monday = expiry - timedelta(days=expiry.weekday())
        for offset in range(5):
            day = monday + timedelta(days=offset)
            if day.weekday() < 5 and day not in holidays:
                out.add(day)
    return out


def ism_dates(year: int) -> dict[date, str]:
    holidays = nyse_holidays(year)
    out = {}
    for month in range(1, 13):
        days = []
        cursor = date(year, month, 1)
        while cursor.month == month:
            if cursor.weekday() < 5 and cursor not in holidays:
                days.append(cursor)
            cursor += timedelta(days=1)
        if days:
            out[days[0]] = "ism_mfg"
        if len(days) >= 3:
            # The services print can land on the manufacturing day only if the
            # month has fewer than three sessions, which it does not.
            out[days[2]] = "ism_svc"
    return out


def load_releases() -> dict:
    return json.loads(_RELEASES.read_text(encoding="utf-8"))


def load_vix() -> dict[date, float]:
    out = {}
    with _VIX.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            out[date.fromisoformat(row["date"])] = float(row["close"])
    return out


def prior_vix(session: date, series: dict[date, float]) -> float | None:
    cursor = session - timedelta(days=1)
    for _ in range(12):
        value = series.get(cursor)
        if value is not None:
            return value
        cursor -= timedelta(days=1)
    return None


def release_session(day: date, holidays: set[date] | None = None) -> date:
    """Cash session that contains the release. A weekend or full close moves forward."""
    holidays = holidays or set()
    cursor = day
    for _ in range(8):
        if cursor.weekday() < 5 and cursor not in holidays:
            return cursor
        cursor += timedelta(days=1)
    return cursor


def tag_sessions(sessions: list[date]) -> dict[date, set[str]]:
    """Tags known before or during the session. VIX uses the prior close."""
    releases = load_releases()
    vix = load_vix()
    years = range(min(sessions).year - 1, max(sessions).year + 2) if sessions else range(2019, 2027)
    holidays = set()
    halves = set()
    expiries: dict[date, str] = {}
    rolls = set()
    ism: dict[date, str] = {}
    for year in years:
        holidays |= nyse_holidays(year)
        halves |= half_days(year)
        expiries.update(opex_dates(year))
        rolls |= roll_week_dates(year)
        ism.update(ism_dates(year))
    by_session: dict[date, set[str]] = {day: set() for day in sessions}
    for kind in ("nfp", "cpi", "ppi", "fomc", "fomc_notation"):
        for text in releases.get(kind, []):
            day = date.fromisoformat(text)
            session = release_session(day, holidays)
            if session in by_session:
                by_session[session].add(kind)
    for day in sessions:
        if day in halves:
            by_session[day].add("half_day")
        kind = expiries.get(day)
        if kind == "quad":
            by_session[day].add("quad")
            by_session[day].add("opex")
        elif kind == "opex":
            by_session[day].add("opex")
        if day in rolls:
            by_session[day].add("roll_week")
        ism_kind = ism.get(day)
        if ism_kind:
            by_session[day].add(ism_kind)
        level = prior_vix(day, vix)
        if level is not None:
            if level >= VIX_HIGH:
                by_session[day].add("vix_high")
            elif level < VIX_LOW:
                by_session[day].add("vix_low")
            else:
                by_session[day].add("vix_mid")
    month_last: dict[tuple[int, int], date] = {}
    for day in sessions:
        month_last[(day.year, day.month)] = day
    for day in month_last.values():
        by_session[day].add("month_end")
    for tags in by_session.values():
        if tags & {"nfp", "cpi", "fomc"}:
            tags.add("red")
    return by_session


def red_days(sessions: list[date]) -> set[date]:
    return {day for day, tags in tag_sessions(sessions).items() if "red" in tags}

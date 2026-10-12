"""Load the Databento minute file into one cash-session per strategy day.

A roll starts a new price series. Prior-day levels are kept only when
yesterday's cash close was the same outright as today's open.
"""

from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from research.calendar import cash_session

_NAN = float("nan")


@dataclass
class Day:
    session: date
    minute: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    vwap: np.ndarray
    atr: np.ndarray
    ema: np.ndarray
    five_end: np.ndarray
    or_high: dict
    or_low: dict
    rth_i: int
    flat_i: int
    exit_on: str
    pdh: float
    pdl: float
    pdc: float
    onh: float
    onl: float
    gap: float
    rth_open: float
    contract: int
    name: str = ""


@dataclass
class _Carry:
    n: int = 0
    ema: float | None = None
    atr: float | None = None
    prev_c: float | None = None
    tr_sum: float = 0.0
    seed: list = field(default_factory=list)


def load_days(paths: list[str]) -> tuple[list[Day], dict]:
    """Read gzipped CSVs. Returns strategy days and a small load summary."""
    ordinals: list[int] = []
    minutes: list[int] = []
    seqs: list[int] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[float] = []
    cids: list[int] = []
    names: dict[str, int] = {}
    id_name: list[str] = []
    for path in paths:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            for row in reader:
                if not row or not row[0]:
                    continue
                text = row[0]
                minute = int(text[11:13]) * 60 + int(text[14:16])
                bar_date = date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
                session = cash_session(bar_date.year, bar_date.month, bar_date.day, minute)
                # Minutes are not a clock that increases all night: 17:00 is 1020
                # and the next midnight is 0. Offset by the calendar day so Friday
                # evening sorts before Sunday evening inside Monday's session.
                seq = (bar_date - session).days * 1440 + minute
                name = row[6].strip() if len(row) > 6 else ""
                cid = names.get(name)
                if cid is None:
                    cid = len(id_name)
                    names[name] = cid
                    id_name.append(name)
                ordinals.append(session.toordinal())
                minutes.append(minute)
                seqs.append(seq)
                opens.append(float(row[1]))
                highs.append(float(row[2]))
                lows.append(float(row[3]))
                closes.append(float(row[4]))
                vols.append(float(row[5]))
                cids.append(cid)
    order = np.lexsort((np.arange(len(ordinals)), np.asarray(seqs), np.asarray(ordinals)))
    ordinals_a = np.asarray(ordinals, dtype=np.int32)[order]
    minutes_a = np.asarray(minutes, dtype=np.int16)[order]
    seqs_a = np.asarray(seqs, dtype=np.int32)[order]
    o = np.asarray(opens, dtype=np.float64)[order]
    h = np.asarray(highs, dtype=np.float64)[order]
    l = np.asarray(lows, dtype=np.float64)[order]
    c = np.asarray(closes, dtype=np.float64)[order]
    v = np.asarray(vols, dtype=np.float64)[order]
    cid_a = np.asarray(cids, dtype=np.int32)[order]
    keep = np.ones(len(order), dtype=bool)
    if len(order) > 1:
        same = (ordinals_a[1:] == ordinals_a[:-1]) & (seqs_a[1:] == seqs_a[:-1])
        keep[:-1] = ~same
    ordinals_a = ordinals_a[keep]
    minutes_a = minutes_a[keep]
    o, h, l, c, v = o[keep], h[keep], l[keep], c[keep], v[keep]
    cid_a = cid_a[keep]
    rolls = int(np.count_nonzero(cid_a[1:] != cid_a[:-1])) if len(cid_a) > 1 else 0
    days = _build_days(ordinals_a, minutes_a, o, h, l, c, v, cid_a, id_name)
    summary = {
        "bars": int(len(o)),
        "sessions": len(days),
        "rolls": rolls,
        "contracts": len(id_name),
        "first": days[0].session.isoformat() if days else "",
        "last": days[-1].session.isoformat() if days else "",
    }
    return days, summary


def _build_days(ordinals, minutes, o, h, l, c, v, cid, id_name) -> list[Day]:
    if len(ordinals) == 0:
        return []
    cuts = np.flatnonzero(ordinals[1:] != ordinals[:-1]) + 1
    bounds = np.concatenate(([0], cuts, [len(ordinals)]))
    carry: dict[int, _Carry] = {}
    prior: dict[int, tuple[date, float, float, float]] = {}
    days: list[Day] = []
    for start, end in zip(bounds[:-1], bounds[1:]):
        session = date.fromordinal(int(ordinals[start]))
        minute = minutes[start:end]
        rth = np.flatnonzero((minute >= 8 * 60 + 30) & (minute < 15 * 60 + 10))
        if len(rth) == 0:
            continue
        rth_local = int(rth[0])
        contract = int(cid[start + rth_local])
        left = rth_local
        while left > 0 and int(cid[start + left - 1]) == contract:
            left -= 1
        right = rth_local + 1
        while right < (end - start) and int(cid[start + right]) == contract:
            right += 1
        sl = slice(start + left, start + right)
        day = _one_day(
            session,
            minute[left:right],
            o[sl],
            h[sl],
            l[sl],
            c[sl],
            v[sl],
            contract,
            id_name[contract] if contract < len(id_name) else "",
            carry.setdefault(contract, _Carry()),
            prior.get(contract),
        )
        days.append(day)
        rth_close = np.flatnonzero((day.minute >= 8 * 60 + 30) & (day.minute < 15 * 60))
        if len(rth_close):
            last = int(rth_close[-1])
            prior[contract] = (
                session,
                float(np.max(day.h[rth_close])),
                float(np.min(day.l[rth_close])),
                float(day.c[last]),
            )
    return days


def _one_day(session, minute, o, h, l, c, v, contract, name, carry: _Carry, previous) -> Day:
    typical = (h + l + c) / 3.0
    vol = np.where(v > 0, v, 0.0)
    denom = np.cumsum(vol)
    numer = np.cumsum(typical * vol)
    vwap = np.divide(numer, denom, out=np.copy(c), where=denom > 0)
    bucket = minute.astype(np.int16) // 5
    if len(bucket) > 1:
        change = np.flatnonzero(bucket[1:] != bucket[:-1]) + 1
    else:
        change = np.array([], dtype=np.int64)
    ends = np.concatenate((change - 1, [len(minute) - 1])).astype(np.int64)
    starts = np.concatenate(([0], change)).astype(np.int64)
    atr = np.full(len(minute), _NAN)
    ema = np.full(len(minute), _NAN)
    last_atr = _NAN
    last_ema = _NAN
    for start_i, end_i in zip(starts, ends):
        hh = float(np.max(h[start_i : end_i + 1]))
        ll = float(np.min(l[start_i : end_i + 1]))
        cc = float(c[end_i])
        ema_now, atr_now = _update(carry, hh, ll, cc)
        if ema_now is not None:
            last_ema = ema_now
        if atr_now is not None:
            last_atr = atr_now
        atr[end_i] = last_atr
        ema[end_i] = last_ema
    _forward(atr)
    _forward(ema)
    or_high = {}
    or_low = {}
    # A bar at or before 08:30 means the cash open is in this slice. Evening
    # bars are later clock numbers, so they do not count as the open.
    open_seen = bool(np.any((minute <= 8 * 60 + 30) & (minute < 12 * 60)))
    for width in (5, 15, 30):
        begin = 8 * 60 + 30
        finish = begin + width
        mask = (minute >= begin) & (minute < finish)
        if not open_seen or not np.any(mask):
            or_high[width] = _NAN
            or_low[width] = _NAN
        else:
            or_high[width] = float(np.max(h[mask]))
            or_low[width] = float(np.min(l[mask]))
    rth = np.flatnonzero((minute >= 8 * 60 + 30) & (minute < 15 * 60 + 10))
    rth_i = int(rth[0])
    flat_hits = np.flatnonzero((minute >= 15 * 60 + 10) & (minute < 17 * 60))
    if len(flat_hits):
        flat_i = int(flat_hits[0])
        exit_on = "open"
    else:
        flat_i = len(minute) - 1
        exit_on = "close"
    if rth_i > 0:
        onh = float(np.max(h[:rth_i]))
        onl = float(np.min(l[:rth_i]))
    else:
        onh = _NAN
        onl = _NAN
    if previous is None or previous[0] >= session:
        pdh = pdl = pdc = gap = _NAN
    else:
        pdh, pdl, pdc = previous[1], previous[2], previous[3]
        gap = float(o[rth_i] - pdc)
    return Day(
        session=session,
        minute=minute,
        o=o,
        h=h,
        l=l,
        c=c,
        v=v,
        vwap=vwap,
        atr=atr,
        ema=ema,
        five_end=ends,
        or_high=or_high,
        or_low=or_low,
        rth_i=rth_i,
        flat_i=flat_i,
        exit_on=exit_on,
        pdh=pdh,
        pdl=pdl,
        pdc=pdc,
        onh=onh,
        onl=onl,
        gap=gap,
        rth_open=float(o[rth_i]),
        contract=contract,
        name=name,
    )


def _update(state: _Carry, high: float, low: float, close: float) -> tuple[float | None, float | None]:
    tr = high - low
    if state.prev_c is not None:
        tr = max(tr, abs(high - state.prev_c), abs(low - state.prev_c))
    state.prev_c = close
    state.n += 1
    if state.ema is None:
        state.seed.append(close)
        if state.n == 20:
            state.ema = sum(state.seed) / 20.0
            state.seed.clear()
    else:
        k = 2.0 / 21.0
        state.ema = close * k + state.ema * (1.0 - k)
    if state.atr is None:
        state.tr_sum += tr
        if state.n == 14:
            state.atr = state.tr_sum / 14.0
    else:
        state.atr = (state.atr * 13.0 + tr) / 14.0
    return state.ema, state.atr


def _forward(values: np.ndarray) -> None:
    last = _NAN
    for i, value in enumerate(values):
        if value == value:
            last = float(value)
        elif last == last:
            values[i] = last

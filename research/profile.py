"""How MES moves by time of day. Descriptive only. Not a strategy search."""

from __future__ import annotations

import numpy as np

from research.calendar import REGIMES

RTH_START = 8 * 60 + 30
RTH_END = 15 * 60


def _mean(values) -> float | None:
    if values is None:
        return None
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return None
    return float(np.mean(arr))


def _median(values) -> float | None:
    if values is None:
        return None
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return None
    return float(np.median(arr))


def _corr(left, right) -> float | None:
    if len(left) < 30:
        return None
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if float(a.std()) == 0.0 or float(b.std()) == 0.0:
        return None
    value = float(np.corrcoef(a, b)[0, 1])
    if value != value:
        return None
    return value


def _vr(left, right) -> float | None:
    if len(left) < 30:
        return None
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    base = float(np.var(a, ddof=1))
    if base <= 0:
        return None
    return float(np.var(a + b, ddof=1) / (2.0 * base))


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _phase(minute: int) -> str:
    if RTH_START <= minute < RTH_END:
        return "rth"
    if minute >= 17 * 60 or minute < RTH_START:
        return "overnight"
    return "after"


def _session_key(minute: int) -> int:
    if minute >= 17 * 60:
        return minute
    return minute + 24 * 60


def _buckets(day) -> list[tuple[int, float, float]]:
    minute = day.minute
    if len(minute) == 0:
        return []
    bucket = (minute.astype(np.int32) // 30) * 30
    cuts = np.flatnonzero(bucket[1:] != bucket[:-1]) + 1 if len(bucket) > 1 else np.array([], dtype=np.int64)
    bounds = np.concatenate(([0], cuts, [len(bucket)]))
    rows = []
    for start, end in zip(bounds[:-1], bounds[1:]):
        if end - start < 20:
            continue
        clock = int(bucket[start])
        signed = float(day.c[end - 1] - day.o[start])
        width = float(np.max(day.h[start:end]) - np.min(day.l[start:end]))
        rows.append((clock, signed, width))
    return rows


def _bar(day, clock: int, field: str):
    hits = np.flatnonzero(day.minute == clock)
    if len(hits) == 0:
        return None
    return float(getattr(day, field)[int(hits[0])])


def _last_before(day, start: int, end: int):
    hits = np.flatnonzero((day.minute >= start) & (day.minute < end))
    if len(hits) == 0:
        return None
    return int(hits[-1])


def build_profile(days) -> dict:
    signed: dict[int, list[float]] = {}
    ranges: dict[int, list[float]] = {}
    pair_a: dict[int, list[float]] = {}
    pair_b: dict[int, list[float]] = {}
    pooled_a: list[float] = []
    pooled_b: list[float] = []
    weekday = {i: [] for i in range(5)}
    gaps_up_next = []
    gaps_down_next = []
    gap_points = []
    rth_returns = []
    first_abs = []
    rest_abs = []
    first_range = []
    rest_range = []
    regime_rows = {name: {"rth": [], "first_abs": [], "open_range": []} for name, _, _ in REGIMES}
    regime_rows["all"] = {"rth": [], "first_abs": [], "open_range": []}

    rth_clocks = list(range(RTH_START, RTH_END, 30))
    for day in days:
        by_clock = {}
        widths = {}
        for clock, ret, width in _buckets(day):
            signed.setdefault(clock, []).append(ret)
            ranges.setdefault(clock, []).append(width)
            by_clock[clock] = ret
            widths[clock] = width
        present = [clock for clock in rth_clocks if clock in by_clock]
        for left, right in zip(present, present[1:]):
            if right != left + 30:
                continue
            pair_a.setdefault(left, []).append(by_clock[left])
            pair_b.setdefault(left, []).append(by_clock[right])
            pooled_a.append(by_clock[left])
            pooled_b.append(by_clock[right])

        open_px = _bar(day, RTH_START, "o")
        last = _last_before(day, RTH_START, RTH_END)
        hour = _last_before(day, RTH_START, 9 * 60 + 30)
        if open_px is None or last is None:
            continue
        rth_ret = float(day.c[last] - open_px)
        rth_returns.append(rth_ret)
        if day.session.weekday() < 5:
            weekday[day.session.weekday()].append(rth_ret)
        if hour is not None:
            first = abs(float(day.c[hour] - open_px))
            rest = abs(float(day.c[last] - day.c[hour]))
            first_abs.append(first)
            rest_abs.append(rest)
            h0 = np.flatnonzero((day.minute >= RTH_START) & (day.minute < 9 * 60 + 30))
            h1 = np.flatnonzero((day.minute >= 9 * 60 + 30) & (day.minute < RTH_END))
            if len(h0):
                first_range.append(float(np.max(day.h[h0]) - np.min(day.l[h0])))
            if len(h1):
                rest_range.append(float(np.max(day.h[h1]) - np.min(day.l[h1])))
        if day.gap == day.gap and RTH_START in by_clock:
            gap_points.append(float(day.gap))
            nxt = by_clock[RTH_START]
            if day.gap > 0:
                gaps_up_next.append(nxt)
            elif day.gap < 0:
                gaps_down_next.append(nxt)

        bucket_range = widths.get(RTH_START)
        facts = {
            "rth": rth_ret,
            "first_abs": first_abs[-1] if hour is not None else None,
            "open_range": bucket_range,
        }
        regime_rows["all"]["rth"].append(rth_ret)
        if facts["first_abs"] is not None:
            regime_rows["all"]["first_abs"].append(facts["first_abs"])
        if bucket_range is not None:
            regime_rows["all"]["open_range"].append(bucket_range)
        for name, start, end in REGIMES:
            if start <= day.session <= end:
                regime_rows[name]["rth"].append(rth_ret)
                if facts["first_abs"] is not None:
                    regime_rows[name]["first_abs"].append(facts["first_abs"])
                if bucket_range is not None:
                    regime_rows[name]["open_range"].append(bucket_range)

    buckets = []
    for clock in sorted(signed, key=_session_key):
        abs_ret = [abs(v) for v in signed[clock]]
        buckets.append(
            {
                "clock": _clock(clock),
                "minute": clock,
                "phase": _phase(clock),
                "n": len(signed[clock]),
                "mean_signed": _mean(signed[clock]),
                "mean_abs": _mean(abs_ret),
                "median_abs": _median(abs_ret),
                "median_range": _median(ranges[clock]),
                "mean_range": _mean(ranges[clock]),
                "autocorr": _corr(pair_a.get(clock, []), pair_b.get(clock, [])),
                "variance_ratio": _vr(pair_a.get(clock, []), pair_b.get(clock, [])),
            }
        )
    rth_buckets = [row for row in buckets if row["phase"] == "rth" and row["n"] >= 200 and row["median_range"] is not None]
    highlight = {}
    if rth_buckets:
        mid = float(np.median([row["median_range"] for row in rth_buckets]))
        widest = max(rth_buckets, key=lambda row: row["median_range"])
        quietest = min(rth_buckets, key=lambda row: row["median_range"])
        lively = [row for row in rth_buckets if row["median_range"] >= mid and row["variance_ratio"] is not None]
        highlight = {
            "median_of_median_range": mid,
            "widest": widest["clock"],
            "widest_range": widest["median_range"],
            "quietest": quietest["clock"],
            "quietest_range": quietest["median_range"],
            "momentum": max(lively, key=lambda row: row["variance_ratio"])["clock"] if lively else None,
            "momentum_vr": max(lively, key=lambda row: row["variance_ratio"])["variance_ratio"] if lively else None,
            "fade": min(lively, key=lambda row: row["variance_ratio"])["clock"] if lively else None,
            "fade_vr": min(lively, key=lambda row: row["variance_ratio"])["variance_ratio"] if lively else None,
        }
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    up = np.asarray(gaps_up_next, dtype=float) if gaps_up_next else np.array([])
    down = np.asarray(gaps_down_next, dtype=float) if gaps_down_next else np.array([])
    return {
        "buckets": buckets,
        "pooled_autocorr": _corr(pooled_a, pooled_b),
        "pooled_variance_ratio": _vr(pooled_a, pooled_b),
        "weekday": [
            {"day": names[i], "n": len(weekday[i]), "mean": _mean(weekday[i]), "median": _median(weekday[i])}
            for i in range(5)
        ],
        "gap": {
            "n": len(gap_points),
            "mean": _mean(gap_points),
            "median": _median(gap_points),
            "up_n": int(len(up)),
            "up_next_mean": _mean(up),
            "up_continuation": float(np.mean(up > 0)) if len(up) else None,
            "down_n": int(len(down)),
            "down_next_mean": _mean(down),
            "down_continuation": float(np.mean(down < 0)) if len(down) else None,
        },
        "rth_mean": _mean(rth_returns),
        "rth_median": _median(rth_returns),
        "first_hour_abs_median": _median(first_abs),
        "rest_abs_median": _median(rest_abs),
        "first_hour_range_median": _median(first_range),
        "rest_range_median": _median(rest_range),
        "regimes": {
            name: {
                "n": len(rows["rth"]),
                "rth_mean": _mean(rows["rth"]),
                "first_hour_abs_median": _median(rows["first_abs"]),
                "open_bucket_range_median": _median(rows["open_range"]),
            }
            for name, rows in regime_rows.items()
        },
        "highlight": highlight,
    }

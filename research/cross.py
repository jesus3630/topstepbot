"""ES/NQ description and four predeclared cross-market rules.

The four rules were written down before this module was scored. They are
not part of the 63-rule menu, and they were not chosen from the MNQ
results. N for their p-values is 4.

1. x_confirm_mes. Trade MES with fh_15bp_1.5R only when MNQ's 8:30–9:29
   return has the same sign and is also at least 15bp.
2. x_confirm_mnq. The mirror: MNQ first-hour momentum only when MES agrees
   by 15bp.
3. x_diverge_fade. If the two first-hour returns have opposite signs and
   each is at least 15bp, fade the one with the larger absolute return.
   Stop at that market's first-hour extreme. Target is its 8:30 open.
   Flat at 12:00 CT. Skip the trade if the 9:30 open is already through
   the target.
4. x_ratio_revert. At 9:30 compare the MNQ/MES price ratio with the median
   of the prior 20 sessions' 15:00 ratios. More than 0.75% above: short
   MNQ and long MES. More than 0.75% below: the opposite. Each leg risks
   $100 with a 0.5% stop. Exit both when a stop is touched, on the next
   open after the close ratio crosses back through the median, or at 15:00.

A cross rule is a candidate only if train has at least 40 trades, positive
expectancy, and positive net, and validation has at least 20 trades,
positive expectancy, positive net, and expectancy at least 25% of train.
The holdout is scored only for candidates. Adjusted p = min(1, raw
validation bootstrap p × 4). Above 0.10 is not a recommendation.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from research.calendar import split_days
from research.costs import MES, MNQ, contracts_for_stop, using_instrument
from research.market import Day
from research.stats import Trade, adjust, bootstrap_p, daily_vector, summarize
from research.strategies import _try_market, all_specs, bar_at, deadline, execute

CONFIRM = 0.0015
RATIO_BAND = 0.0075
RATIO_LOOKBACK = 20
RATIO_STOP = 0.005
RATIO_RISK = 100.0
N_CROSS = 4
RULES = ("x_confirm_mes", "x_confirm_mnq", "x_diverge_fade", "x_ratio_revert")


def _fh_spec():
    return next(spec for spec in all_specs() if spec.name == "fh_15bp_1.5R")


def first_hour_return(day: Day) -> float | None:
    """8:30 open to the close of the last bar before 9:30, as a fraction of price."""
    open_i = bar_at(day, 8 * 60 + 30)
    entry_i = bar_at(day, 9 * 60 + 30)
    if open_i is None or entry_i is None or entry_i <= open_i:
        return None
    base = float(day.o[open_i])
    if abs(base) < 1:
        return None
    return (float(day.c[entry_i - 1]) - base) / abs(base)


def _index(day: Day) -> dict[int, int]:
    return {int(minute): i for i, minute in enumerate(day.minute.tolist())}


def cash_move(day: Day) -> dict | None:
    """8:30 open to the last cash bar before 15:10."""
    idx = _index(day)
    open_i = idx.get(8 * 60 + 30)
    last = None
    for minute, i in idx.items():
        if 8 * 60 + 30 <= minute < 15 * 60 + 10:
            if last is None or minute > last[0]:
                last = (minute, i)
    if open_i is None or last is None:
        return None
    op = float(day.o[open_i])
    cl = float(day.c[last[1]])
    if op == 0:
        return None
    return {"open": op, "close": cl, "points": cl - op, "ret": (cl - op) / op, "minute": last[0]}


def _slip(side: str, price: float, tick: float) -> float:
    return price + tick if side == "long" else price - tick


def _stop_price(side: str, open_, high, low, stop: float, tick: float) -> float | None:
    if side == "long":
        if low > stop + 1e-9:
            return None
        raw = open_ if open_ <= stop + 1e-9 else stop
        return _slip("short", raw, tick)
    if high < stop - 1e-9:
        return None
    raw = open_ if open_ >= stop - 1e-9 else stop
    return _slip("long", raw, tick)


def _close_ratio(day_mes: Day, day_mnq: Day) -> float | None:
    mes = cash_move(day_mes)
    mnq = cash_move(day_mnq)
    if mes is None or mnq is None or mes["close"] == 0:
        return None
    return mnq["close"] / mes["close"]


def run_confirm(days: list[Day], other: dict[date, Day], instrument, name: str) -> list[Trade]:
    spec = _fh_spec()
    chosen = []
    for day in days:
        peer = other.get(day.session)
        if peer is None:
            continue
        left = first_hour_return(day)
        right = first_hour_return(peer)
        if left is None or right is None or left == 0 or right == 0:
            continue
        if (left > 0) != (right > 0) or abs(left) < CONFIRM or abs(right) < CONFIRM:
            continue
        chosen.append(day)
    from research.strategies import run_days

    trades = run_days(chosen, spec, instrument=instrument)
    return [Trade(name, t.session, t.side, t.qty, t.entry, t.exit, t.pnl, t.mae, t.reason) for t in trades]


def run_diverge(mes_days: list[Day], mnq_by: dict[date, Day], mes_by: dict[date, Day]) -> list[Trade]:
    del mes_by
    trades: list[Trade] = []
    for mes in mes_days:
        mnq = mnq_by.get(mes.session)
        if mnq is None:
            continue
        mes_ret = first_hour_return(mes)
        mnq_ret = first_hour_return(mnq)
        if mes_ret is None or mnq_ret is None or mes_ret * mnq_ret >= 0:
            continue
        if abs(mes_ret) < CONFIRM or abs(mnq_ret) < CONFIRM:
            continue
        if abs(mes_ret) >= abs(mnq_ret):
            day, ret, inst = mes, mes_ret, MES
        else:
            day, ret, inst = mnq, mnq_ret, MNQ
        side = "short" if ret > 0 else "long"
        open_i = bar_at(day, 8 * 60 + 30)
        entry_i = bar_at(day, 9 * 60 + 30)
        if open_i is None or entry_i is None:
            continue
        base = float(day.o[open_i])
        if side == "short":
            stop = float(np.max(day.h[open_i:entry_i]))
        else:
            stop = float(np.min(day.l[open_i:entry_i]))
        last_i, exit_on = deadline(day, 12 * 60)
        with using_instrument(inst):
            intent = _try_market(day, side, entry_i, stop, base, last_i, exit_on)
            if intent:
                trades.extend(execute(day, [intent], "x_diverge_fade"))
    return trades


def _ratio_trade(mes: Day, mnq: Day, median: float) -> Trade | None:
    mes_idx = _index(mes)
    mnq_idx = _index(mnq)
    entry_m = 9 * 60 + 30
    if entry_m not in mes_idx or entry_m not in mnq_idx or not (median > 0):
        return None
    mes_i = mes_idx[entry_m]
    mnq_i = mnq_idx[entry_m]
    mes_raw = float(mes.o[mes_i])
    mnq_raw = float(mnq.o[mnq_i])
    if mes_raw <= 0 or mnq_raw <= 0:
        return None
    ratio = mnq_raw / mes_raw
    if ratio > median * (1 + RATIO_BAND):
        mes_side, mnq_side = "long", "short"
        rich = True
    elif ratio < median * (1 - RATIO_BAND):
        mes_side, mnq_side = "short", "long"
        rich = False
    else:
        return None
    mes_qty = contracts_for_stop(
        RATIO_STOP * mes_raw,
        risk=RATIO_RISK,
        fee=MES.fee_round_turn,
        tick=MES.tick,
        point_value=MES.point_value,
    )
    mnq_qty = contracts_for_stop(
        RATIO_STOP * mnq_raw,
        risk=RATIO_RISK,
        fee=MNQ.fee_round_turn,
        tick=MNQ.tick,
        point_value=MNQ.point_value,
    )
    if mes_qty < 1 or mnq_qty < 1:
        return None
    mes_entry = _slip(mes_side, mes_raw, MES.tick)
    mnq_entry = _slip(mnq_side, mnq_raw, MNQ.tick)
    mes_stop = mes_entry * (1 - RATIO_STOP) if mes_side == "long" else mes_entry * (1 + RATIO_STOP)
    mnq_stop = mnq_entry * (1 - RATIO_STOP) if mnq_side == "long" else mnq_entry * (1 + RATIO_STOP)
    clocks = sorted(set(mes_idx) & set(mnq_idx))
    clocks = [m for m in clocks if entry_m <= m < 15 * 60 + 10]
    if not clocks:
        return None
    mes_exit = mnq_exit = None
    reason = "time"
    pending = False
    for pos, minute in enumerate(clocks):
        mi = mes_idx[minute]
        ni = mnq_idx[minute]
        if pending:
            mes_exit = _slip(mes_side, float(mes.o[mi]), MES.tick)
            mnq_exit = _slip(mnq_side, float(mnq.o[ni]), MNQ.tick)
            reason = "ratio"
            break
        mes_hit = _stop_price(mes_side, float(mes.o[mi]), float(mes.h[mi]), float(mes.l[mi]), mes_stop, MES.tick)
        mnq_hit = _stop_price(mnq_side, float(mnq.o[ni]), float(mnq.h[ni]), float(mnq.l[ni]), mnq_stop, MNQ.tick)
        if mes_hit is not None or mnq_hit is not None:
            mes_exit = mes_hit if mes_hit is not None else _slip(mes_side, float(mes.o[mi]), MES.tick)
            mnq_exit = mnq_hit if mnq_hit is not None else _slip(mnq_side, float(mnq.o[ni]), MNQ.tick)
            reason = "stop"
            break
        close_ratio = float(mnq.c[ni]) / float(mes.c[mi]) if float(mes.c[mi]) else None
        crossed = close_ratio is not None and ((rich and close_ratio <= median) or ((not rich) and close_ratio >= median))
        if crossed and pos + 1 < len(clocks):
            pending = True
            continue
        if crossed:
            mes_exit = _slip(mes_side, float(mes.c[mi]), MES.tick)
            mnq_exit = _slip(mnq_side, float(mnq.c[ni]), MNQ.tick)
            reason = "ratio"
            break
    if mes_exit is None or mnq_exit is None:
        minute = clocks[-1]
        mes_exit = _slip(mes_side, float(mes.c[mes_idx[minute]]), MES.tick)
        mnq_exit = _slip(mnq_side, float(mnq.c[mnq_idx[minute]]), MNQ.tick)
        reason = "time"

    def leg(side, entry, exit_px, qty, inst):
        points = (exit_px - entry) if side == "long" else (entry - exit_px)
        return points * inst.point_value * qty - inst.fee_round_turn * qty

    pnl = leg(mes_side, mes_entry, mes_exit, mes_qty, MES) + leg(mnq_side, mnq_entry, mnq_exit, mnq_qty, MNQ)
    return Trade(
        name="x_ratio_revert",
        session=mes.session,
        side=mes_side,
        qty=int(mes_qty + mnq_qty),
        entry=mes_entry,
        exit=mes_exit,
        pnl=pnl,
        mae=max(0.0, -pnl),
        reason=reason,
    )


def run_ratio(mes_days: list[Day], mnq_by: dict[date, Day], history: list[tuple[date, float]]) -> list[Trade]:
    """`history` is every matched session's 15:00 ratio, in date order, including days outside this split."""
    lookup = {day: ratio for day, ratio in history}
    ordered = [day for day, _ratio in history]
    trades = []
    for mes in mes_days:
        if mes.session not in lookup or mes.session not in mnq_by:
            continue
        prior = [lookup[day] for day in ordered if day < mes.session][-RATIO_LOOKBACK:]
        if len(prior) < RATIO_LOOKBACK:
            continue
        median = float(np.median(prior))
        trade = _ratio_trade(mes, mnq_by[mes.session], median)
        if trade is not None:
            trades.append(trade)
    return trades


def cross_candidate(train: dict, val: dict) -> bool:
    if train["trades"] < 40 or train["expectancy"] <= 0 or train["net"] <= 0:
        return False
    if val["trades"] < 20 or val["expectancy"] <= 0 or val["net"] <= 0:
        return False
    if train["expectancy"] > 0 and val["expectancy"] < 0.25 * train["expectancy"]:
        return False
    return True


def score_cross(mes_days: list[Day], mnq_days: list[Day]) -> dict:
    """Train and validation for every rule. Holdout only after the gate."""
    mes_train, mes_val, mes_hold = split_days(mes_days)
    mnq_train, mnq_val, mnq_hold = split_days(mnq_days)
    mes_by = {day.session: day for day in mes_days}
    mnq_by = {day.session: day for day in mnq_days}
    history = []
    for day in mes_days:
        peer = mnq_by.get(day.session)
        if peer is None:
            continue
        ratio = _close_ratio(day, peer)
        if ratio is not None:
            history.append((day.session, ratio))
    blocks = {
        "train": (mes_train, mnq_train),
        "validation": (mes_val, mnq_val),
        "holdout": (mes_hold, mnq_hold),
    }
    out = {}
    for name in RULES:
        scored = {}
        trades = {}
        for label in ("train", "validation"):
            trades[label] = _run_named(name, *blocks[label], mes_by, mnq_by, history)
            scored[label] = summarize(trades[label])
        ok = cross_candidate(scored["train"], scored["validation"])
        if ok:
            trades["holdout"] = _run_named(name, *blocks["holdout"], mes_by, mnq_by, history)
            scored["holdout"] = summarize(trades["holdout"])
            sessions = sorted(
                {day.session for day in blocks["validation"][0]} | {day.session for day in blocks["validation"][1]}
            )
            raw = bootstrap_p(daily_vector(trades["validation"], sessions))
            pvalue = {"bootstrap": raw, "bootstrap_adjusted": adjust(raw, N_CROSS)}
        else:
            scored["holdout"] = None
            pvalue = None
        out[name] = {"stats": scored, "candidate": ok, "pvalue": pvalue, "trades": trades}
    return out


def _run_named(name, mes_days, mnq_days, mes_by, mnq_by, history) -> list[Trade]:
    if name == "x_confirm_mes":
        return run_confirm(mes_days, mnq_by, MES, name)
    if name == "x_confirm_mnq":
        return run_confirm(mnq_days, mes_by, MNQ, name)
    if name == "x_diverge_fade":
        return run_diverge(mes_days, mnq_by, mes_by)
    if name == "x_ratio_revert":
        return run_ratio(mes_days, mnq_by, history)
    raise KeyError(name)


def _corr(left, right) -> float | None:
    if len(left) < 20:
        return None
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if float(a.std()) == 0 or float(b.std()) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _clock_bucket(minute: int) -> int:
    """Half-hour label for a return that ends at `minute`.

    8:31 through 8:59 land on 8:30. 14:30 through 14:59 land on 14:30.
    The cash open itself has no prior cash minute, so it is not a return.
    """
    return (minute // 30) * 30


def _minute_returns(day: Day) -> dict[int, float]:
    """Close-to-close return for exact consecutive cash minutes. No gap bridging."""
    idx = _index(day)
    out = {}
    minutes = sorted(m for m in idx if 8 * 60 + 30 <= m < 15 * 60)
    for prev, minute in zip(minutes, minutes[1:]):
        if minute != prev + 1:
            continue
        a = float(day.c[idx[prev]])
        b = float(day.c[idx[minute]])
        if a:
            out[minute] = (b - a) / a
    return out


def describe_pair(mes_days: list[Day], mnq_days: list[Day], vix_bucket: dict[date, str] | None = None) -> dict:
    mes_by = {day.session: day for day in mes_days}
    mnq_by = {day.session: day for day in mnq_days}
    sessions = sorted(set(mes_by) & set(mnq_by))
    daily = []
    ratios = []
    diverge_first = 0
    diverge_day = 0
    first_n = 0
    for session in sessions:
        mes = cash_move(mes_by[session])
        mnq = cash_move(mnq_by[session])
        if mes is None or mnq is None:
            continue
        daily.append(
            {
                "session": session,
                "mes_ret": mes["ret"],
                "mnq_ret": mnq["ret"],
                "mes_pts": mes["points"],
                "mnq_pts": mnq["points"],
                "ratio": mnq["close"] / mes["close"],
            }
        )
        ratios.append(mnq["close"] / mes["close"])
        if mes["ret"] * mnq["ret"] < 0:
            diverge_day += 1
        left = first_hour_return(mes_by[session])
        right = first_hour_return(mnq_by[session])
        if left is not None and right is not None:
            first_n += 1
            if left * right < 0:
                diverge_first += 1
    mes_rets = [row["mes_ret"] for row in daily]
    mnq_rets = [row["mnq_ret"] for row in daily]
    mes_pts = np.asarray([row["mes_pts"] for row in daily], dtype=float)
    mnq_pts = np.asarray([row["mnq_pts"] for row in daily], dtype=float)
    by_year = {}
    for year in sorted({row["session"].year for row in daily}):
        chunk = [row for row in daily if row["session"].year == year]
        by_year[str(year)] = {
            "n": len(chunk),
            "corr": _corr([row["mes_ret"] for row in chunk], [row["mnq_ret"] for row in chunk]),
        }
    by_vix = {}
    if vix_bucket:
        for label in ("vix_low", "vix_mid", "vix_high"):
            chunk = [row for row in daily if vix_bucket.get(row["session"]) == label]
            by_vix[label] = {
                "n": len(chunk),
                "corr": _corr([row["mes_ret"] for row in chunk], [row["mnq_ret"] for row in chunk]),
            }
    rolling = []
    window = 60
    for i in range(window, len(daily) + 1):
        chunk = daily[i - window : i]
        rolling.append(
            {
                "session": chunk[-1]["session"].isoformat(),
                "corr": _corr([row["mes_ret"] for row in chunk], [row["mnq_ret"] for row in chunk]),
            }
        )
    # Lead-lag and intraday correlations on a sample of aligned 1-minute returns.
    lag_pairs = {lag: [[], []] for lag in range(-5, 6)}
    bucket_pairs = {bucket: [[], []] for bucket in range(8 * 60 + 30, 15 * 60, 30)}
    five = [[], []]
    thirty = [[], []]
    for session in sessions:
        mes_r = _minute_returns(mes_by[session])
        mnq_r = _minute_returns(mnq_by[session])
        clocks = sorted(set(mes_r) & set(mnq_r))
        for minute in clocks:
            for lag in lag_pairs:
                other = mnq_r.get(minute + lag)
                if other is None:
                    continue
                lag_pairs[lag][0].append(mes_r[minute])
                lag_pairs[lag][1].append(other)
            bucket = _clock_bucket(minute)
            if bucket in bucket_pairs:
                bucket_pairs[bucket][0].append(mes_r[minute])
                bucket_pairs[bucket][1].append(mnq_r[minute])
        mes_idx = _index(mes_by[session])
        mnq_idx = _index(mnq_by[session])
        for step, sink in ((5, five), (30, thirty)):
            start = 8 * 60 + 30
            while start + step < 15 * 60:
                mes_path = _path_return(mes_by[session], start, start + step, mes_idx)
                mnq_path = _path_return(mnq_by[session], start, start + step, mnq_idx)
                if mes_path is not None and mnq_path is not None:
                    sink[0].append(mes_path)
                    sink[1].append(mnq_path)
                start += step
    lags = {str(lag): {"corr": _corr(*pairs), "n": len(pairs[0])} for lag, pairs in lag_pairs.items()}
    by_clock = []
    for bucket, pairs in bucket_pairs.items():
        by_clock.append(
            {
                "clock": f"{bucket // 60:02d}:{bucket % 60:02d}",
                "corr": _corr(*pairs),
                "n": len(pairs[0]),
            }
        )
    ratio_arr = np.asarray(ratios, dtype=float)
    ratio_changes = np.diff(ratio_arr) if len(ratio_arr) > 2 else np.asarray([])
    phi = None
    if len(ratio_changes) > 30 and float(np.std(ratio_changes[:-1])) > 0:
        phi = float(np.corrcoef(ratio_changes[:-1], ratio_changes[1:])[0, 1])
    return {
        "matched_sessions": len(sessions),
        "daily_n": len(daily),
        "daily_corr": _corr(mes_rets, mnq_rets),
        "five_minute_corr": _corr(*five),
        "thirty_minute_corr": _corr(*thirty),
        "one_minute_corr": lags.get("0"),
        "lead_lag": lags,
        "by_clock": by_clock,
        "by_year": by_year,
        "by_vix": by_vix,
        "rolling_60": rolling,
        "diverge_day_share": (diverge_day / len(daily)) if daily else None,
        "diverge_first_hour_share": (diverge_first / first_n) if first_n else None,
        "vol": {
            "mes_points": float(mes_pts.std(ddof=1)) if len(mes_pts) > 2 else None,
            "mnq_points": float(mnq_pts.std(ddof=1)) if len(mnq_pts) > 2 else None,
            "mes_dollars": float(mes_pts.std(ddof=1) * MES.point_value) if len(mes_pts) > 2 else None,
            "mnq_dollars": float(mnq_pts.std(ddof=1) * MNQ.point_value) if len(mnq_pts) > 2 else None,
        },
        "ratio": {
            "first": ratios[0] if ratios else None,
            "last": ratios[-1] if ratios else None,
            "median": float(np.median(ratio_arr)) if len(ratio_arr) else None,
            "change_autocorr": phi,
            "series": [{"session": row["session"].isoformat(), "ratio": row["ratio"]} for row in daily],
        },
    }


def _path_return(day: Day, start: int, end: int, idx: dict[int, int] | None = None) -> float | None:
    idx = _index(day) if idx is None else idx
    a = idx.get(start)
    b = idx.get(end)
    if a is None or b is None:
        return None
    left = float(day.c[a]) if start != 8 * 60 + 30 else float(day.o[a])
    right = float(day.c[b])
    if not left:
        return None
    return (right - left) / left

"""Four predeclared cross-market predictors. Written before this module was scored.

These rules are not part of the 63-rule menu and not part of the Sunday menu.
N for their p-values is 4. A candidate needs 40 train trades and 20 validation
trades, positive expectancy and net in both, and validation expectancy at least
25% of train. The holdout is scored only for candidates. Adjusted p = min(1,
raw validation bootstrap p × 4). Above 0.10 is not a recommendation.

Information has to be known before the entry. Today's Treasury yield and today's
VIX close are not used.

1. x_m2k_on_mes. At 8:30 CT, compare the overnight return of M2K with the
   overnight return of MES. Overnight is the prior cash close (same outright)
   to the 8:30 open, so Monday includes the weekend. If M2K outperformed MES
   by at least 15bp, buy MES. If MES outperformed M2K by at least 15bp, sell
   MES. Stop is 15bp of price. Target is 1.5R. Flat at 9:30 CT.
2. x_m2k_div_mes. If the MES and M2K first hours (8:30 to the bar before 9:30)
   have opposite signs and each is at least 15bp, fade MES. Stop at the MES
   first-hour extreme. Target is the MES 8:30 open. Flat at 12:00 CT. Skip the
   trade if the 9:30 open is already through the target.
3. x_vix_up_fh_mes. Trade MES with fh_15bp_1.5R only when the prior VIX close
   is at least 1.0 point above the VIX close before that. Both closes are
   known before the session. The 20/15 level tags from the earlier event study
   are not a filter here.
4. x_yield_on_mnq. Use the prior change in the Treasury 10-year par yield
   (yesterday's print minus the print before it). Yields down 5bp or more:
   buy MNQ at 8:30. Yields up 5bp or more: sell MNQ. Stop is 15bp of price.
   Target is 1.5R. Flat at 9:30 CT. The yield series is the Treasury daily
   par yield curve, which is the series FRED calls DGS10. FRED itself returned
   HTTP 500 on 2026-10-12. No ZN futures were downloaded.

VIX futures intraday was not free from Cboe on that date (the history file
returned HTTP 403). No VX rule was added in its place.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np

from research.calendar import split_days
from research.costs import MES, MNQ, M2K, using_instrument
from research.cross import _clock_bucket, _corr, _minute_returns, first_hour_return
from research.market import Day
from research.stats import Trade, adjust, bootstrap_p, daily_vector, signflip_p, summarize
from research.strategies import _try_market, all_specs, bar_at, deadline, execute

CONFIRM = 0.0015
STOP_RETURN = 0.0015
TARGET_R = 1.5
VIX_JUMP = 1.0
YIELD_MOVE = 0.05
N_PREDICTORS = 4
RULES = ("x_m2k_on_mes", "x_m2k_div_mes", "x_vix_up_fh_mes", "x_yield_on_mnq")
DGS10_PATH = Path(__file__).resolve().parent / "data" / "dgs10.csv"


def load_dgs10(path: Path | None = None) -> dict[date, float]:
    text = (path or DGS10_PATH).read_text(encoding="utf-8").splitlines()
    out = {}
    for line in text[1:]:
        if not line.strip():
            continue
        raw_day, raw_value = line.split(",")[:2]
        out[date.fromisoformat(raw_day)] = float(raw_value)
    return out


def prior_yield_change(session: date, series: dict[date, float]) -> float | None:
    """Change between the two latest prints strictly before `session`.

    The print on `session` itself is ignored, so a same-day yield cannot
    predict that morning.
    """
    found: list[float] = []
    cursor = session - timedelta(days=1)
    for _ in range(30):
        value = series.get(cursor)
        if value is not None:
            found.append(value)
            if len(found) == 2:
                return found[0] - found[1]
        cursor -= timedelta(days=1)
    return None


def overnight_return(day: Day) -> float | None:
    if day.pdc != day.pdc or day.rth_open != day.rth_open or abs(day.pdc) < 1:
        return None
    return (float(day.rth_open) - float(day.pdc)) / abs(float(day.pdc))


def _fh_spec():
    return next(spec for spec in all_specs() if spec.name == "fh_15bp_1.5R")


def _open_bracket(day: Day, side: str, name: str, instrument) -> list[Trade]:
    entry_i = bar_at(day, 8 * 60 + 30)
    if entry_i is None:
        return []
    last_i, exit_on = deadline(day, 9 * 60 + 30)
    ref = float(day.o[entry_i])
    distance = STOP_RETURN * ref
    if side == "long":
        stop, target = ref - distance, ref + TARGET_R * distance
    else:
        stop, target = ref + distance, ref - TARGET_R * distance
    with using_instrument(instrument):
        intent = _try_market(day, side, entry_i, stop, target, last_i, exit_on)
        if not intent:
            return []
        return execute(day, [intent], name)


def run_m2k_overnight(mes_days: list[Day], m2k_by: dict[date, Day]) -> list[Trade]:
    trades: list[Trade] = []
    for mes in mes_days:
        peer = m2k_by.get(mes.session)
        if peer is None:
            continue
        mes_ret = overnight_return(mes)
        m2k_ret = overnight_return(peer)
        if mes_ret is None or m2k_ret is None:
            continue
        relative = m2k_ret - mes_ret
        if relative >= CONFIRM:
            side = "long"
        elif relative <= -CONFIRM:
            side = "short"
        else:
            continue
        trades.extend(_open_bracket(mes, side, "x_m2k_on_mes", MES))
    return trades


def run_m2k_divergence(mes_days: list[Day], m2k_by: dict[date, Day]) -> list[Trade]:
    trades: list[Trade] = []
    for mes in mes_days:
        peer = m2k_by.get(mes.session)
        if peer is None:
            continue
        mes_ret = first_hour_return(mes)
        m2k_ret = first_hour_return(peer)
        if mes_ret is None or m2k_ret is None or mes_ret * m2k_ret >= 0:
            continue
        if abs(mes_ret) < CONFIRM or abs(m2k_ret) < CONFIRM:
            continue
        side = "short" if mes_ret > 0 else "long"
        open_i = bar_at(mes, 8 * 60 + 30)
        entry_i = bar_at(mes, 9 * 60 + 30)
        if open_i is None or entry_i is None:
            continue
        base = float(mes.o[open_i])
        stop = float(np.max(mes.h[open_i:entry_i])) if side == "short" else float(np.min(mes.l[open_i:entry_i]))
        last_i, exit_on = deadline(mes, 12 * 60)
        with using_instrument(MES):
            intent = _try_market(mes, side, entry_i, stop, base, last_i, exit_on)
            if intent:
                trades.extend(execute(mes, [intent], "x_m2k_div_mes"))
    return trades


def run_vix_filter(mes_days: list[Day], vix: dict[date, float]) -> list[Trade]:
    chosen = []
    for day in mes_days:
        change = prior_yield_change(day.session, vix)
        if change is not None and change >= VIX_JUMP:
            chosen.append(day)
    with using_instrument(MES):
        trades = execute_spec(chosen, "x_vix_up_fh_mes")
    return trades


def execute_spec(days: list[Day], name: str) -> list[Trade]:
    from research.strategies import run_days

    trades = run_days(days, _fh_spec(), instrument=MES)
    return [Trade(name, t.session, t.side, t.qty, t.entry, t.exit, t.pnl, t.mae, t.reason) for t in trades]


def run_yield(mnq_days: list[Day], yields: dict[date, float]) -> list[Trade]:
    trades: list[Trade] = []
    for day in mnq_days:
        change = prior_yield_change(day.session, yields)
        if change is None or abs(change) < YIELD_MOVE:
            continue
        side = "long" if change <= -YIELD_MOVE else "short"
        trades.extend(_open_bracket(day, side, "x_yield_on_mnq", MNQ))
    return trades


def predictor_candidate(train: dict, val: dict) -> bool:
    if train["trades"] < 40 or train["expectancy"] <= 0 or train["net"] <= 0:
        return False
    if val["trades"] < 20 or val["expectancy"] <= 0 or val["net"] <= 0:
        return False
    if train["expectancy"] > 0 and val["expectancy"] < 0.25 * train["expectancy"]:
        return False
    return True


def score_predictors(mes_days, mnq_days, m2k_days, vix, yields) -> dict:
    mes_by = {day.session: day for day in mes_days}
    m2k_by = {day.session: day for day in m2k_days}
    blocks = {
        "train": split_days(mes_days)[0],
        "validation": split_days(mes_days)[1],
        "holdout": split_days(mes_days)[2],
    }
    mnq_blocks = {
        "train": split_days(mnq_days)[0],
        "validation": split_days(mnq_days)[1],
        "holdout": split_days(mnq_days)[2],
    }
    out = {}
    for name in RULES:
        scored = {}
        trades = {}
        for label in ("train", "validation"):
            trades[label] = _run_predictor(name, label, blocks, mnq_blocks, m2k_by, vix, yields)
            scored[label] = summarize(trades[label])
        ok = predictor_candidate(scored["train"], scored["validation"])
        if ok:
            trades["holdout"] = _run_predictor(name, "holdout", blocks, mnq_blocks, m2k_by, vix, yields)
            scored["holdout"] = summarize(trades["holdout"])
            sessions = _sessions(name, "validation", blocks, mnq_blocks)
            daily = daily_vector(trades["validation"], sessions)
            raw = bootstrap_p(daily)
            flipped = signflip_p(daily)
            pvalue = {
                "bootstrap": raw,
                "bootstrap_adjusted": adjust(raw, N_PREDICTORS),
                "signflip": flipped,
                "signflip_adjusted": adjust(flipped, N_PREDICTORS),
            }
        else:
            scored["holdout"] = None
            pvalue = None
        out[name] = {"stats": scored, "candidate": ok, "pvalue": pvalue}
    return out


def _sessions(name: str, label: str, blocks, mnq_blocks) -> list[date]:
    days = mnq_blocks[label] if name == "x_yield_on_mnq" else blocks[label]
    return [day.session for day in days]


def _run_predictor(name, label, blocks, mnq_blocks, m2k_by, vix, yields) -> list[Trade]:
    if name == "x_m2k_on_mes":
        return run_m2k_overnight(blocks[label], m2k_by)
    if name == "x_m2k_div_mes":
        return run_m2k_divergence(blocks[label], m2k_by)
    if name == "x_vix_up_fh_mes":
        return run_vix_filter(blocks[label], vix)
    if name == "x_yield_on_mnq":
        return run_yield(mnq_blocks[label], yields)
    raise KeyError(name)


def signal_correlations(mes_days, mnq_days, m2k_days, vix, yields) -> dict:
    """Descriptive associations. These are not extra traded rules."""
    m2k_by = {day.session: day for day in m2k_days}
    out = {}
    for label, days in zip(("train", "validation", "holdout", "all"), (*split_days(mes_days), mes_days)):
        rel, fh, vix_chg, vix_fh, yld, nq_fh = [], [], [], [], [], []
        mnq_by = {day.session: day for day in mnq_days}
        for mes in days:
            peer = m2k_by.get(mes.session)
            mes_on = overnight_return(mes)
            mes_fh = first_hour_return(mes)
            if peer is not None and mes_on is not None and mes_fh is not None:
                m2k_on = overnight_return(peer)
                if m2k_on is not None:
                    rel.append(m2k_on - mes_on)
                    fh.append(mes_fh)
            change = prior_yield_change(mes.session, vix)
            if change is not None and mes_fh is not None:
                vix_chg.append(change)
                vix_fh.append(mes_fh)
            nq = mnq_by.get(mes.session)
            nq_first = first_hour_return(nq) if nq is not None else None
            move = prior_yield_change(mes.session, yields)
            if move is not None and nq_first is not None:
                yld.append(move)
                nq_fh.append(nq_first)
        out[label] = {
            "m2k_minus_mes_overnight_vs_mes_first_hour": {"corr": _corr(rel, fh), "n": len(rel)},
            "prior_vix_change_vs_mes_first_hour": {"corr": _corr(vix_chg, vix_fh), "n": len(vix_chg)},
            "prior_yield_change_vs_mnq_first_hour": {"corr": _corr(yld, nq_fh), "n": len(nq_fh)},
        }
    return out


def pair_clock(left_days: list[Day], right_days: list[Day], left_value: float, right_value: float) -> dict:
    """Cash-session correlation of two micros. Dollar vol uses each point value."""
    left_by = {day.session: day for day in left_days}
    right_by = {day.session: day for day in right_days}
    sessions = sorted(set(left_by) & set(right_by))
    daily_l, daily_r, pts_l, pts_r = [], [], [], []
    for session in sessions:
        left = _cash(left_by[session])
        right = _cash(right_by[session])
        if left is None or right is None:
            continue
        daily_l.append(left["ret"])
        daily_r.append(right["ret"])
        pts_l.append(left["points"])
        pts_r.append(right["points"])
    bucket_pairs = {bucket: [[], []] for bucket in range(8 * 60 + 30, 15 * 60, 30)}
    five = [[], []]
    thirty = [[], []]
    one = [[], []]
    lags = {lag: [[], []] for lag in range(-5, 6)}
    for session in sessions:
        left_r = _minute_returns(left_by[session])
        right_r = _minute_returns(right_by[session])
        for minute, value in left_r.items():
            other = right_r.get(minute)
            if other is None:
                continue
            one[0].append(value)
            one[1].append(other)
            bucket = _clock_bucket(minute)
            if bucket in bucket_pairs:
                bucket_pairs[bucket][0].append(value)
                bucket_pairs[bucket][1].append(other)
            for lag in lags:
                shifted = right_r.get(minute + lag)
                if shifted is None:
                    continue
                lags[lag][0].append(value)
                lags[lag][1].append(shifted)
        left_idx = {int(minute): i for i, minute in enumerate(left_by[session].minute.tolist())}
        right_idx = {int(minute): i for i, minute in enumerate(right_by[session].minute.tolist())}
        for step, sink in ((5, five), (30, thirty)):
            start = 8 * 60 + 30
            while start + step < 15 * 60:
                a = _path(left_by[session], start, start + step, left_idx)
                b = _path(right_by[session], start, start + step, right_idx)
                if a is not None and b is not None:
                    sink[0].append(a)
                    sink[1].append(b)
                start += step
    pts_l_a = np.asarray(pts_l, dtype=float)
    pts_r_a = np.asarray(pts_r, dtype=float)
    clocks = []
    for bucket, pairs in bucket_pairs.items():
        clocks.append({"clock": f"{bucket // 60:02d}:{bucket % 60:02d}", "corr": _corr(*pairs), "n": len(pairs[0])})
    return {
        "matched_sessions": len(sessions),
        "daily_n": len(daily_l),
        "daily_corr": _corr(daily_l, daily_r),
        "five_minute_corr": _corr(*five),
        "thirty_minute_corr": _corr(*thirty),
        "one_minute_corr": _corr(*one),
        "one_minute_n": len(one[0]),
        "lead_lag": {str(lag): {"corr": _corr(*pairs), "n": len(pairs[0])} for lag, pairs in lags.items()},
        "by_clock": clocks,
        "vol": {
            "left_points": float(pts_l_a.std(ddof=1)) if len(pts_l_a) > 2 else None,
            "right_points": float(pts_r_a.std(ddof=1)) if len(pts_r_a) > 2 else None,
            "left_dollars": float(pts_l_a.std(ddof=1) * left_value) if len(pts_l_a) > 2 else None,
            "right_dollars": float(pts_r_a.std(ddof=1) * right_value) if len(pts_r_a) > 2 else None,
        },
    }


def _cash(day: Day) -> dict | None:
    idx = {int(minute): i for i, minute in enumerate(day.minute.tolist())}
    open_i = idx.get(8 * 60 + 30)
    last = None
    for minute, i in idx.items():
        if 8 * 60 + 30 <= minute < 15 * 60 + 10 and (last is None or minute > last[0]):
            last = (minute, i)
    if open_i is None or last is None:
        return None
    op = float(day.o[open_i])
    cl = float(day.c[last[1]])
    if not op:
        return None
    return {"points": cl - op, "ret": (cl - op) / op}


def _path(day: Day, start: int, end: int, idx: dict[int, int]) -> float | None:
    a = idx.get(start)
    b = idx.get(end)
    if a is None or b is None:
        return None
    left = float(day.o[a]) if start == 8 * 60 + 30 else float(day.c[a])
    right = float(day.c[b])
    if not left:
        return None
    return (right - left) / left


def m2k_point_value() -> float:
    return M2K.point_value

"""Predeclared MES intraday rules. The grid in this file is the search.

Nothing here reads live settings or sends orders. Parameter lists are fixed.
Do not add a family after seeing results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from research.costs import (
    DAILY_STOP,
    MAX_TRADES_PER_DAY,
    active_instrument,
    contracts_for_stop,
    resolve_instrument,
    round_tick,
    using_instrument,
)
from research.market import Day
from research.simulate import simulate
from research.stats import Trade, corr, train_ok, val_ok

# A finalist whose Bonferroni-adjusted validation bootstrap p-value is above
# this cutoff is not a trading recommendation. The cutoff does not drop the
# rule from the holdout report.
P_VALUE_CUTOFF = 0.10

ORB_MINUTES = (5, 15, 30)
ORB_STOPS = ("opposite", "mid", "atr")
ORB_TARGETS = ("1R", "2R", "time")
GAP_THRESHOLDS = (0.001, 0.0025)
GAP_MODES = ("fade", "go")
MR_WINDOWS = (
    (8 * 60 + 30, 10 * 60 + 30),
    (10 * 60 + 30, 12 * 60),
    (12 * 60, 14 * 60),
)
MR_BANDS = (1.0, 1.5)
PB_WINDOWS = (
    (8 * 60 + 45, 11 * 60),
    (9 * 60 + 30, 12 * 60),
)
PB_MULTIPLES = (1.5, 2.0)
PD_VARIANTS = ("fade", "break_1R", "break_2R")
ON_TARGETS = ("mid", "1.5R")
FH_THRESHOLDS = (0.0015, 0.0025)
FH_EXITS = ("1.5R", "time")
LATE_EXITS = ("1R", "time")
TOD_WINDOWS = (
    (8 * 60 + 30, 9 * 60 + 30),
    (9 * 60 + 30, 10 * 60 + 30),
    (10 * 60, 11 * 60 + 30),
    (13 * 60 + 30, 15 * 60),
    (14 * 60, 15 * 60),
)
TOD_SIDES = ("long", "short")

AXES = {
    "orb": {"or_minutes": ORB_MINUTES, "stop": ORB_STOPS, "target": ORB_TARGETS},
    "gap": {"threshold": GAP_THRESHOLDS, "mode": GAP_MODES},
    "vwap_mr": {"window": MR_WINDOWS, "band": MR_BANDS},
    "vwap_pb": {"window": PB_WINDOWS, "multiple": PB_MULTIPLES},
    "prior": {"variant": PD_VARIANTS},
    "overnight": {"target": ON_TARGETS},
    "first_hour": {"threshold": FH_THRESHOLDS, "exit": FH_EXITS},
    "late": {"exit": LATE_EXITS},
    "tod": {"window": TOD_WINDOWS, "side": TOD_SIDES},
    "trend": {"rule": ("vwap_ema_20",)},
}


@dataclass(frozen=True)
class Spec:
    name: str
    family: str
    params: tuple
    eligible: bool = True

    def get(self, key: str):
        for name, value in self.params:
            if name == key:
                return value
        raise KeyError(key)


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}{minute % 60:02d}"


def _bp(threshold: float) -> str:
    return f"{round(threshold * 10000):.0f}bp"


def all_specs() -> list[Spec]:
    specs: list[Spec] = []
    for width in ORB_MINUTES:
        for stop in ORB_STOPS:
            for target in ORB_TARGETS:
                specs.append(
                    Spec(
                        f"orb_{width}_{stop}_{target}",
                        "orb",
                        (("or_minutes", width), ("stop", stop), ("target", target)),
                    )
                )
    for mode in GAP_MODES:
        for threshold in GAP_THRESHOLDS:
            specs.append(
                Spec(
                    f"gap_{mode}_{_bp(threshold)}",
                    "gap",
                    (("mode", mode), ("threshold", threshold)),
                )
            )
    for window in MR_WINDOWS:
        for band in MR_BANDS:
            specs.append(
                Spec(
                    f"vwap_mr_{_hhmm(window[0])}_{_hhmm(window[1])}_{band:g}",
                    "vwap_mr",
                    (("window", window), ("band", band)),
                )
            )
    for window in PB_WINDOWS:
        for multiple in PB_MULTIPLES:
            specs.append(
                Spec(
                    f"vwap_pb_{_hhmm(window[0])}_{_hhmm(window[1])}_{multiple:g}R",
                    "vwap_pb",
                    (("window", window), ("multiple", multiple)),
                )
            )
    for variant in PD_VARIANTS:
        specs.append(Spec(f"pd_{variant}", "prior", (("variant", variant),)))
    for target in ON_TARGETS:
        specs.append(Spec(f"on_reclaim_{target}", "overnight", (("target", target),)))
    for threshold in FH_THRESHOLDS:
        for exit_mode in FH_EXITS:
            specs.append(
                Spec(
                    f"fh_{_bp(threshold)}_{exit_mode}",
                    "first_hour",
                    (("threshold", threshold), ("exit", exit_mode)),
                )
            )
    for exit_mode in LATE_EXITS:
        specs.append(Spec(f"late_{exit_mode}", "late", (("exit", exit_mode),)))
    for window in TOD_WINDOWS:
        for side in TOD_SIDES:
            specs.append(
                Spec(
                    f"tod_{_hhmm(window[0])}_{_hhmm(window[1])}_{side}",
                    "tod",
                    (("window", window), ("side", side)),
                )
            )
    specs.append(Spec("trend_vwap_ema", "trend", (("rule", "vwap_ema_20"),)))
    specs.append(Spec("bench_bh", "bench", (("kind", "bh"),), eligible=False))
    specs.append(Spec("bench_bh_stop", "bench", (("kind", "bh_stop"),), eligible=False))
    specs.append(make_random(7))
    return specs


def make_random(seed: int) -> Spec:
    name = "bench_random" if seed == 7 else f"bench_random_{seed}"
    return Spec(name, "bench", (("kind", "random"), ("seed", seed)), eligible=False)


def eligible_count(specs: list[Spec] | None = None) -> int:
    specs = all_specs() if specs is None else specs
    return sum(1 for spec in specs if spec.eligible)


def grid_text(instrument=None) -> str:
    inst = active_instrument() if instrument is None else resolve_instrument(instrument)
    tick_dollars = inst.tick * inst.point_value
    costs = (
        f"Costs: TopstepX {inst.code} round turn is ${inst.fee_round_turn:.2f} ({inst.fee_note}). "
        f"Market and stop orders pay 1 tick (${tick_dollars:.2f}) of adverse slippage per side. "
        "Limit orders pay none, and only if price trades through the limit. "
        "A market entry sized off a stop therefore includes 2 ticks plus the fee. "
        "A limit entry includes 1 tick (the stop or the flatten) plus the fee. "
        "The live bot's $1.40 placeholder is not changed."
    )
    specs = all_specs()
    lines = [
        f"Predeclared {inst.code} research grid. Written from research/strategies.py before any backtest result is used.",
        "",
        "Splits: train is 2019-05-05 through 2023-12-31. Validation is 2024-01-01 through 2025-06-30. Holdout is 2025-07-01 through the end of the file. The holdout is scored only for rules that already qualified on train and validation, and it is not used to pick a replacement.",
        "",
        costs,
        "",
        f"Risk used only in this study: $200 per trade, at most 50 {inst.code}, at most 2 trades a day, no new trade after the day's closed P&L or worst mark is -$400 or worse, flat by 15:10 CT, one position at a time, cash session only. Overnight drift is described and not traded.",
        "",
        "Selection, fixed before the run: train needs at least 100 trades, positive expectancy, and positive net. Validation needs at least 40 trades, positive expectancy, positive net, a win rate within 15 percentage points of train, and expectancy at least 25% of train. A neighbor is the same family with exactly one parameter changed to the next value on that parameter's list. If more than half of the existing neighbors have validation expectancy at or below zero, the rule is dropped. Remaining rules are ranked by validation expectancy times the square root of validation trades. Keep up to 3 with pairwise validation daily correlation under 0.5. Benchmarks cannot be finalists.",
        "",
        "Multiple testing: N is the number of eligible configurations, not the benchmarks and not the 100 random seeds. Adjusted p = min(1, raw bootstrap p × N). The bootstrap resamples validation daily P&L 2000 times with seed 12345. A sign-flip check uses the same seed on its own generator. The random null uses seeds 1000 through 1099, train and validation only, and is not a menu to pick from. A finalist with adjusted bootstrap p above 0.10 is not a trading recommendation.",
        "",
        "Combine, scored separately inside each split so later years do not leak backward: start $50,000, pass at +$3,000 if the best day is under half of profits, fail on a $1,000 intraday daily loss or an end-of-day trailing $2,000 max loss. The floor is min($50,000, high-water mark − $2,000) and only moves up. Attempts start on the first session of each month. Unfinished attempts count as not passed. The 40% consistency figure is the later payout path, not the Combine pass test.",
        "",
        f"Eligible configurations: {eligible_count(specs)}. Total including 3 benchmarks: {len(specs)}.",
        "",
        "| name | family | parameters | eligible |",
        "|---|---|---|---|",
    ]
    for spec in specs:
        params = ", ".join(f"{key}={value}" for key, value in spec.params)
        lines.append(f"| {spec.name} | {spec.family} | {params} | {spec.eligible} |")
    lines.append("")
    return "\n".join(lines)


def _atr(day: Day, index: int) -> float | None:
    if index < 0 or index >= len(day.atr):
        return None
    value = float(day.atr[index])
    if value != value or value < _tick():
        return None
    return value


def _fives(day: Day):
    cached = getattr(day, "_five_cache", None)
    if cached is not None:
        return cached
    ends = np.asarray(day.five_end, dtype=np.int64)
    count = len(ends)
    starts = np.empty(count, dtype=np.int64)
    highs = np.empty(count, dtype=np.float64)
    lows = np.empty(count, dtype=np.float64)
    if count:
        starts[0] = 0
        if count > 1:
            starts[1:] = ends[:-1] + 1
        for i in range(count):
            a = int(starts[i])
            b = int(ends[i]) + 1
            highs[i] = float(np.max(day.h[a:b]))
            lows[i] = float(np.min(day.l[a:b]))
    cached = (starts, ends, highs, lows)
    day._five_cache = cached
    return cached


def bar_at(day: Day, clock: int) -> int | None:
    hits = np.flatnonzero(day.minute == clock)
    if len(hits) == 0:
        return None
    index = int(hits[0])
    if index > day.flat_i:
        return None
    return index


def deadline(day: Day, clock: int) -> tuple[int, str]:
    if clock >= 15 * 60 + 10:
        return day.flat_i, day.exit_on
    hits = np.flatnonzero((day.minute >= clock) & (day.minute < 17 * 60))
    if len(hits) == 0:
        return day.flat_i, day.exit_on
    index = int(hits[0])
    if index >= day.flat_i:
        return day.flat_i, day.exit_on
    return index, "open"


def _earlier(left: tuple[int, str], right: tuple[int, str]) -> tuple[int, str]:
    if left[0] != right[0]:
        return left if left[0] < right[0] else right
    if left[1] == "open":
        return left
    return right


def _bracket_ok(side: str, ref: float, stop: float, target: float | None) -> bool:
    if side == "long":
        if not (stop < ref - 1e-9):
            return False
        if target is not None and not (target > ref + 1e-9):
            return False
    else:
        if not (stop > ref + 1e-9):
            return False
        if target is not None and not (target < ref - 1e-9):
            return False
    return True


def _tick() -> float:
    return active_instrument().tick


def _round(price: float) -> float:
    return round_tick(price, _tick())


def _qty(distance: float, slip_ticks: int) -> int:
    inst = active_instrument()
    if distance <= inst.tick:
        return 0
    return contracts_for_stop(
        distance,
        fee=inst.fee_round_turn,
        tick=inst.tick,
        point_value=inst.point_value,
        slip_ticks_round_turn=slip_ticks,
    )


def _try_market(
    day: Day,
    side: str,
    entry_i: int | None,
    stop: float | None,
    target: float | None,
    last_i: int,
    exit_on: str,
    qty: int | None = None,
    slip_ticks: int = 2,
):
    if entry_i is None or entry_i < 0 or entry_i >= len(day.o):
        return None
    if last_i < 0 or last_i >= len(day.o):
        return None
    if exit_on == "open" and entry_i >= last_i:
        return None
    if exit_on == "close" and entry_i > last_i:
        return None
    ref = float(day.o[entry_i])
    if stop is not None:
        stop = _round(float(stop))
    if target is not None:
        target = _round(float(target))
    if qty is None:
        if stop is None or not _bracket_ok(side, ref, stop, target):
            return None
        distance = (ref - stop) if side == "long" else (stop - ref)
        qty = _qty(distance, slip_ticks)
        if qty < 1:
            return None
    else:
        if stop is not None and not _bracket_ok(side, ref, stop, target):
            return None
        qty = int(qty)
        if qty < 1:
            return None
    return {
        "side": side,
        "entry_index": int(entry_i),
        "kind": "market",
        "level": None,
        "stop": stop,
        "target": target,
        "last_index": int(last_i),
        "exit_on": exit_on,
        "qty": int(qty),
    }


def _try_limit(day: Day, side: str, entry_i: int, level: float, stop: float, target: float, last_i: int, exit_on: str):
    if entry_i < 0 or entry_i >= len(day.o) or last_i < 0 or last_i >= len(day.o):
        return None
    if exit_on == "open" and entry_i >= last_i:
        return None
    level = _round(float(level))
    stop = _round(float(stop))
    target = _round(float(target))
    if not _bracket_ok(side, level, stop, target):
        return None
    distance = (level - stop) if side == "long" else (stop - level)
    qty = _qty(distance, slip_ticks=1)
    if qty < 1:
        return None
    return {
        "side": side,
        "entry_index": int(entry_i),
        "kind": "limit",
        "level": level,
        "stop": stop,
        "target": target,
        "last_index": int(last_i),
        "exit_on": exit_on,
        "qty": int(qty),
    }


def _r_target(side: str, ref: float, stop: float, multiple: float) -> float:
    distance = abs(ref - stop)
    if side == "long":
        return ref + multiple * distance
    return ref - multiple * distance


def execute(day: Day, intents: list[dict], name: str) -> list[Trade]:
    taken = 0
    closed = 0.0
    worst = 0.0
    trades: list[Trade] = []
    for intent in intents:
        if taken >= MAX_TRADES_PER_DAY or worst <= -DAILY_STOP or closed <= -DAILY_STOP:
            break
        if int(intent["qty"]) < 1:
            continue
        inst = active_instrument()
        result = simulate(
            day.o,
            day.h,
            day.l,
            day.c,
            side=intent["side"],
            entry_index=intent["entry_index"],
            kind=intent["kind"],
            level=intent["level"],
            stop=intent["stop"],
            target=intent["target"],
            last_index=intent["last_index"],
            exit_on=intent["exit_on"],
            qty=intent["qty"],
            fee_rt=inst.fee_round_turn,
            tick=inst.tick,
            point_value=inst.point_value,
        )
        if not result.filled:
            continue
        heat = closed - result.mae
        closed += result.pnl
        worst = min(worst, heat, closed)
        taken += 1
        trades.append(
            Trade(
                name=name,
                session=day.session,
                side=intent["side"],
                qty=int(intent["qty"]),
                entry=result.entry,
                exit=result.exit,
                pnl=result.pnl,
                mae=result.mae,
                reason=result.reason,
            )
        )
    return trades


def run_days(days: list[Day], spec: Spec, instrument=None) -> list[Trade]:
    """Replay `spec`. `instrument` is an Instrument or a code such as ``MNQ``.

    Omitted means the active instrument, which is MES unless a caller set one.
    """
    chosen = active_instrument() if instrument is None else resolve_instrument(instrument)
    with using_instrument(chosen):
        runner = RUNNERS[spec.family]
        trades: list[Trade] = []
        for day in days:
            intents = runner(day, spec)
            if intents:
                trades.extend(execute(day, intents, spec.name))
        return trades


def run_orb(day: Day, spec: Spec) -> list[dict]:
    width = spec.get("or_minutes")
    stop_mode = spec.get("stop")
    target_mode = spec.get("target")
    hi = float(day.or_high.get(width, np.nan))
    lo = float(day.or_low.get(width, np.nan))
    if not (hi > lo):
        return []
    or_end = 8 * 60 + 30 + width
    starts, ends, _, _ = _fives(day)
    for i in range(len(ends)):
        start_minute = int(day.minute[int(starts[i])])
        if start_minute < or_end or start_minute >= 15 * 60:
            continue
        end_i = int(ends[i])
        close = float(day.c[end_i])
        if close > hi:
            side = "long"
        elif close < lo:
            side = "short"
        else:
            continue
        entry_i = end_i + 1
        if entry_i >= len(day.o) or entry_i > day.flat_i:
            return []
        if stop_mode == "opposite":
            stop = lo if side == "long" else hi
        elif stop_mode == "mid":
            stop = (hi + lo) / 2.0
        else:
            atr = _atr(day, end_i)
            if atr is None:
                return []
            stop = close - atr if side == "long" else close + atr
        ref = float(day.o[entry_i])
        entry_minute = int(day.minute[entry_i])
        if target_mode == "time":
            target = None
            last_i, exit_on = deadline(day, entry_minute + 90)
        else:
            multiple = 1.0 if target_mode == "1R" else 2.0
            target = _r_target(side, ref, stop, multiple)
            last_i, exit_on = deadline(day, entry_minute + 120)
        intent = _try_market(day, side, entry_i, stop, target, last_i, exit_on)
        return [intent] if intent else []
    return []


def run_gap(day: Day, spec: Spec) -> list[dict]:
    if day.gap != day.gap or day.pdc != day.pdc or abs(float(day.pdc)) < 1:
        return []
    gap = float(day.gap)
    if abs(gap) < float(spec.get("threshold")) * abs(float(day.pdc)):
        return []
    entry_i = bar_at(day, 8 * 60 + 30)
    if entry_i is None or gap == 0:
        return []
    ref = float(day.o[entry_i])
    if gap > 0:
        fade, go = "short", "long"
    else:
        fade, go = "long", "short"
    side = fade if spec.get("mode") == "fade" else go
    distance = abs(gap)
    if spec.get("mode") == "fade":
        target = float(day.pdc)
        stop = ref + distance if side == "short" else ref - distance
    else:
        stop = float(day.pdc)
        target = ref + distance if side == "long" else ref - distance
    last_i, exit_on = deadline(day, 10 * 60 + 30)
    intent = _try_market(day, side, entry_i, stop, target, last_i, exit_on)
    return [intent] if intent else []


def run_vwap_mr(day: Day, spec: Spec) -> list[dict]:
    win_start, win_end = spec.get("window")
    band = float(spec.get("band"))
    armed = None
    extreme = None
    starts, ends, highs, lows = _fives(day)
    for i in range(len(ends)):
        start_minute = int(day.minute[int(starts[i])])
        if start_minute < win_start or start_minute >= win_end:
            continue
        end_i = int(ends[i])
        atr = _atr(day, end_i)
        vwap = float(day.vwap[end_i])
        if atr is None or vwap != vwap:
            continue
        close = float(day.c[end_i])
        upper = vwap + band * atr
        lower = vwap - band * atr
        if armed is None:
            if close > upper:
                armed = "short"
                extreme = float(highs[i])
            elif close < lower:
                armed = "long"
                extreme = float(lows[i])
            continue
        if armed == "short":
            extreme = max(extreme, float(highs[i]))
        else:
            extreme = min(extreme, float(lows[i]))
        if close > upper + 1e-9 or close < lower - 1e-9:
            continue
        side = armed
        stop = extreme + _tick() if side == "short" else extreme - _tick()
        entry_i = end_i + 1
        last_i, exit_on = deadline(day, win_end)
        intent = _try_market(day, side, entry_i, stop, vwap, last_i, exit_on)
        return [intent] if intent else []
    return []


def run_vwap_pb(day: Day, spec: Spec) -> list[dict]:
    win_start, win_end = spec.get("window")
    multiple = float(spec.get("multiple"))
    starts, ends, _, _ = _fives(day)
    for i in range(len(ends)):
        start_minute = int(day.minute[int(starts[i])])
        if start_minute < win_start or start_minute >= win_end:
            continue
        end_i = int(ends[i])
        atr = _atr(day, end_i)
        vwap = float(day.vwap[end_i])
        ema = float(day.ema[end_i])
        if atr is None or vwap != vwap or ema != ema:
            continue
        close = float(day.c[end_i])
        if close > vwap and close > ema:
            side = "long"
            stop = vwap - atr
        elif close < vwap and close < ema:
            side = "short"
            stop = vwap + atr
        else:
            continue
        target = _r_target(side, vwap, stop, multiple)
        last_i, exit_on = deadline(day, win_end)
        intent = _try_limit(day, side, end_i + 1, vwap, stop, target, last_i, exit_on)
        return [intent] if intent else []
    return []


def run_prior(day: Day, spec: Spec) -> list[dict]:
    if day.pdh != day.pdh or day.pdl != day.pdl or day.pdc != day.pdc:
        return []
    if not (float(day.pdl) < float(day.pdh)):
        return []
    variant = spec.get("variant")
    if variant == "fade":
        win_start, win_end = 8 * 60 + 45, 11 * 60 + 30
    else:
        win_start, win_end = 8 * 60 + 45, 12 * 60
    multiple = 2.0 if variant == "break_2R" else 1.0
    starts, ends, highs, lows = _fives(day)
    for i in range(len(ends)):
        start_minute = int(day.minute[int(starts[i])])
        if start_minute < win_start or start_minute >= win_end:
            continue
        end_i = int(ends[i])
        close = float(day.c[end_i])
        entry_i = end_i + 1
        if entry_i >= len(day.o) or entry_i > day.flat_i:
            return []
        if variant == "fade":
            swept_high = float(highs[i]) > float(day.pdh)
            swept_low = float(lows[i]) < float(day.pdl)
            inside = float(day.pdl) < close < float(day.pdh)
            if swept_high and swept_low:
                continue
            if swept_high and inside:
                side = "short"
                stop = float(highs[i]) + _tick()
            elif swept_low and inside:
                side = "long"
                stop = float(lows[i]) - _tick()
            else:
                continue
            target = float(day.pdc)
            last_i, exit_on = day.flat_i, day.exit_on
        else:
            if close > float(day.pdh):
                side = "long"
            elif close < float(day.pdl):
                side = "short"
            else:
                continue
            ref = float(day.o[entry_i])
            mid = (float(day.pdh) + float(day.pdl)) / 2.0
            if side == "long":
                if float(day.pdc) < ref - _tick():
                    stop = float(day.pdc)
                elif mid < ref - _tick():
                    stop = mid
                else:
                    return []
            else:
                if float(day.pdc) > ref + _tick():
                    stop = float(day.pdc)
                elif mid > ref + _tick():
                    stop = mid
                else:
                    return []
            target = _r_target(side, ref, stop, multiple)
            last_i, exit_on = deadline(day, int(day.minute[entry_i]) + 120)
        intent = _try_market(day, side, entry_i, stop, target, last_i, exit_on)
        return [intent] if intent else []
    return []


def run_overnight(day: Day, spec: Spec) -> list[dict]:
    if day.onh != day.onh or day.onl != day.onl or not (float(day.onh) > float(day.onl)):
        return []
    mid = (float(day.onh) + float(day.onl)) / 2.0
    starts, ends, highs, lows = _fives(day)
    for i in range(len(ends)):
        start_minute = int(day.minute[int(starts[i])])
        if start_minute < 8 * 60 + 30 or start_minute >= 10 * 60 + 30:
            continue
        end_i = int(ends[i])
        close = float(day.c[end_i])
        swept_high = float(highs[i]) > float(day.onh) and close < float(day.onh)
        swept_low = float(lows[i]) < float(day.onl) and close > float(day.onl)
        if swept_high == swept_low:
            continue
        entry_i = end_i + 1
        if entry_i >= len(day.o) or entry_i > day.flat_i:
            return []
        ref = float(day.o[entry_i])
        if swept_high:
            side = "short"
            stop = float(highs[i]) + _tick()
        else:
            side = "long"
            stop = float(lows[i]) - _tick()
        if spec.get("target") == "mid":
            target = mid
        else:
            target = _r_target(side, ref, stop, 1.5)
        intent = _try_market(day, side, entry_i, stop, target, day.flat_i, day.exit_on)
        return [intent] if intent else []
    return []


def run_first_hour(day: Day, spec: Spec) -> list[dict]:
    open_i = bar_at(day, 8 * 60 + 30)
    entry_i = bar_at(day, 9 * 60 + 30)
    if open_i is None or entry_i is None or entry_i <= open_i:
        return []
    base = float(day.o[open_i])
    if abs(base) < 1:
        return []
    move = float(day.c[entry_i - 1]) - base
    if abs(move) < float(spec.get("threshold")) * abs(base):
        return []
    side = "long" if move > 0 else "short"
    if side == "long":
        stop = float(np.min(day.l[open_i:entry_i]))
    else:
        stop = float(np.max(day.h[open_i:entry_i]))
    ref = float(day.o[entry_i])
    target = None if spec.get("exit") == "time" else _r_target(side, ref, stop, 1.5)
    last_i, exit_on = deadline(day, 15 * 60)
    intent = _try_market(day, side, entry_i, stop, target, last_i, exit_on)
    return [intent] if intent else []


def run_late(day: Day, spec: Spec) -> list[dict]:
    entry_i = bar_at(day, 13 * 60 + 30)
    if entry_i is None:
        return []
    _, ends, _, _ = _fives(day)
    sig = None
    for end in ends:
        end = int(end)
        if end < entry_i and int(day.minute[end]) < 13 * 60 + 30:
            sig = end
    if sig is None:
        return []
    atr = _atr(day, sig)
    vwap = float(day.vwap[sig])
    ema = float(day.ema[sig])
    close = float(day.c[sig])
    if atr is None or vwap != vwap or ema != ema:
        return []
    if close > vwap and close > ema:
        side = "long"
    elif close < vwap and close < ema:
        side = "short"
    else:
        return []
    ref = float(day.o[entry_i])
    stop = ref - atr if side == "long" else ref + atr
    target = None if spec.get("exit") == "time" else _r_target(side, ref, stop, 1.0)
    last_i, exit_on = deadline(day, 15 * 60)
    intent = _try_market(day, side, entry_i, stop, target, last_i, exit_on)
    return [intent] if intent else []


def run_tod(day: Day, spec: Spec) -> list[dict]:
    start, end = spec.get("window")
    side = spec.get("side")
    entry_i = bar_at(day, start)
    if entry_i is None:
        return []
    atr = _atr(day, entry_i)
    if atr is None:
        return []
    ref = float(day.o[entry_i])
    stop = ref - 2.0 * atr if side == "long" else ref + 2.0 * atr
    last_i, exit_on = deadline(day, end)
    intent = _try_market(day, side, entry_i, stop, None, last_i, exit_on)
    return [intent] if intent else []


def run_trend(day: Day, spec: Spec) -> list[dict]:
    del spec
    starts, ends, highs, lows = _fives(day)
    for i in range(1, len(ends)):
        start_minute = int(day.minute[int(starts[i])])
        if start_minute < 8 * 60 + 45 or start_minute >= 11 * 60 + 30:
            continue
        end_i = int(ends[i])
        atr = _atr(day, end_i)
        vwap = float(day.vwap[end_i])
        ema = float(day.ema[end_i])
        if atr is None or vwap != vwap or ema != ema:
            continue
        close = float(day.c[end_i])
        if close > vwap and close > ema and close > float(highs[i - 1]):
            side = "long"
        elif close < vwap and close < ema and close < float(lows[i - 1]):
            side = "short"
        else:
            continue
        entry_i = end_i + 1
        if entry_i >= len(day.o) or entry_i > day.flat_i:
            return []
        ref = float(day.o[entry_i])
        stop = ref - atr if side == "long" else ref + atr
        target = _r_target(side, ref, stop, 2.0)
        last_i, exit_on = _earlier(deadline(day, int(day.minute[entry_i]) + 90), deadline(day, 12 * 60))
        intent = _try_market(day, side, entry_i, stop, target, last_i, exit_on)
        return [intent] if intent else []
    return []


def run_bench(day: Day, spec: Spec) -> list[dict]:
    kind = spec.get("kind")
    if kind == "random":
        return _random(day, int(spec.get("seed")))
    entry_i = bar_at(day, 8 * 60 + 30)
    if entry_i is None:
        return []
    last_i, exit_on = deadline(day, 15 * 60)
    ref = float(day.o[entry_i])
    if kind == "bh":
        intent = _try_market(day, "long", entry_i, None, None, last_i, exit_on, qty=1)
    else:
        intent = _try_market(day, "long", entry_i, ref - 20.0, None, last_i, exit_on)
    return [intent] if intent else []


def _random(day: Day, seed: int) -> list[dict]:
    entry_i = bar_at(day, 9 * 60)
    if entry_i is None:
        return []
    side = "long" if (day.session.toordinal() * 17 + seed) % 2 == 0 else "short"
    ref = float(day.o[entry_i])
    if side == "long":
        stop, target = ref - 8.0, ref + 8.0
    else:
        stop, target = ref + 8.0, ref - 8.0
    last_i, exit_on = deadline(day, 10 * 60 + 30)
    intent = _try_market(day, side, entry_i, stop, target, last_i, exit_on)
    return [intent] if intent else []


RUNNERS = {
    "orb": run_orb,
    "gap": run_gap,
    "vwap_mr": run_vwap_mr,
    "vwap_pb": run_vwap_pb,
    "prior": run_prior,
    "overnight": run_overnight,
    "first_hour": run_first_hour,
    "late": run_late,
    "tod": run_tod,
    "trend": run_trend,
    "bench": run_bench,
}


def neighbors_of(spec: Spec, specs: list[Spec]) -> list[Spec]:
    axes = AXES.get(spec.family)
    if not axes:
        return []
    mine = dict(spec.params)
    found = []
    for other in specs:
        if not other.eligible or other.family != spec.family or other.name == spec.name:
            continue
        theirs = dict(other.params)
        if set(mine) != set(theirs):
            continue
        diffs = [key for key in mine if mine[key] != theirs[key]]
        if len(diffs) != 1:
            continue
        key = diffs[0]
        axis = axes.get(key)
        if not axis or mine[key] not in axis or theirs[key] not in axis:
            continue
        if abs(axis.index(mine[key]) - axis.index(theirs[key])) == 1:
            found.append(other)
    return found


def neighbor_stable(spec: Spec, specs: list[Spec], val_stats: dict) -> bool:
    neigh = neighbors_of(spec, specs)
    if not neigh:
        return True
    bad = sum(1 for item in neigh if val_stats[item.name]["expectancy"] <= 0)
    return bad <= len(neigh) / 2


def select(specs: list[Spec], train_stats: dict, val_stats: dict, val_daily: dict) -> list[Spec]:
    """Pick finalists from train and validation only. There is no holdout argument."""
    pool = []
    for spec in specs:
        if not spec.eligible:
            continue
        train = train_stats[spec.name]
        val = val_stats[spec.name]
        if not (train_ok(train) and val_ok(train, val) and neighbor_stable(spec, specs, val_stats)):
            continue
        score = val["expectancy"] * math.sqrt(val["trades"])
        pool.append((score, spec))
    pool.sort(key=lambda item: (-item[0], item[1].name))
    picked: list[Spec] = []
    for _, spec in pool:
        if all(corr(val_daily[spec.name], val_daily[prev.name]) < 0.5 for prev in picked):
            picked.append(spec)
        if len(picked) == 3:
            break
    return picked

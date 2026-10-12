"""Sunday 5:00 PM CT reopen. Rules were written before this module was scored.

Topstep's hours page, read 2026-10-12, opens trading Sunday at 5:00 PM CT and
requires a flat book by 3:10 PM CT each weekday. Friday is closed until that
Sunday open. The page does not print the sentence "Sunday P&L counts as
Monday." The window that starts Sunday 5:00 PM CT ends Monday 3:10 PM CT, and
the CME trade date for that window is Monday. This study assigns each Sunday
evening to that Monday for the split and for daily P&L.

Four rules, each run on MES, MNQ, and M2K. N is 12. A candidate needs 30 train
trades and 15 validation trades, positive expectancy and net in both, and
validation expectancy at least 25% of train. Holdout is scored only for
candidates. Adjusted p = min(1, raw validation bootstrap p × 12). Above 0.10
is not a recommendation. These 12 are not added to the 63-rule menu.

1. sun_gap_fade. Gap is the first Sunday print versus Friday's cash close on
   the same outright. A roll skips the trade. Enter on the next bar, opposite
   the gap. Stop is one gap-width beyond the entry bar's open. Target is
   Friday's close. Flat at Monday 8:30 CT. Skip a gap smaller than 4 ticks.
2. sun_gap_go. The same entry, in the direction of the gap. Stop is Friday's
   close. Target is one gap-width past the entry bar's open. Flat at Monday
   8:30 CT.
3. sun_orb30. Range is Sunday 17:00–17:29 CT. Enter the bar after the first
   1-minute close outside that range. Stop is the opposite side. Target is 1R.
   Flat at 21:00 CT Sunday.
4. sun_fade_hour. At 18:00 CT, if the first-hour close is in the top quarter
   of the 17:00–17:59 range, sell; in the bottom quarter, buy. Otherwise skip.
   Stop is that extreme. Target is the Sunday open. Flat at 21:00 CT.

Fill rates and volume comparisons are descriptive. They are not extra rules.
"""

from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from research.calendar import cash_session, split_name
from research.costs import using_instrument
from research.market import Day
from research.stats import Trade, adjust, bootstrap_p, daily_vector, signflip_p, summarize
from research.strategies import _try_market, execute

MIN_GAP_TICKS = 4
N_SUNDAY = 12
RULES = ("sun_gap_fade", "sun_gap_go", "sun_orb30", "sun_fade_hour")
OPEN = 17 * 60
HOUR = 18 * 60
ORB_END = 17 * 60 + 30
EVENING_FLAT = 21 * 60
MORNING_FLAT = 8 * 60 + 30
QUARTER = 0.25


@dataclass
class Episode:
    sunday: date
    trade_date: date
    next_open: date | None
    friday_close: float | None
    friday_date: date | None
    contract: str
    rolled: bool
    minute: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray

    @property
    def gap(self) -> float | None:
        if self.friday_close is None or len(self.o) == 0 or self.rolled:
            return None
        return float(self.o[0] - self.friday_close)


def load_episodes(paths: list[str]) -> tuple[list[Episode], dict]:
    """Sunday reopen through the next cash open, plus volume comparisons."""
    by_date: dict[date, list[tuple]] = {}
    for path in paths:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            for row in reader:
                if not row or not row[0]:
                    continue
                text = row[0]
                day = date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
                minute = int(text[11:13]) * 60 + int(text[14:16])
                by_date.setdefault(day, []).append(
                    (minute, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]), row[6].strip())
                )
    dates = sorted(by_date)
    friday_close: dict[date, tuple[float, str]] = {}
    for day in dates:
        if day.weekday() >= 5:
            continue
        cash = [bar for bar in by_date[day] if 8 * 60 + 30 <= bar[0] < 15 * 60]
        if cash:
            last = cash[-1]
            friday_close[day] = (last[4], last[6])
    episodes: list[Episode] = []
    sunday_vol = []
    weekday_evening_vol = []
    cash_vol = []
    sunday_range = []
    cash_range = []
    for day in dates:
        if day.weekday() < 4:
            evening = [bar[5] for bar in by_date[day] if OPEN <= bar[0] < HOUR]
            if evening:
                weekday_evening_vol.append(float(sum(evening)))
        if day.weekday() < 5:
            cash = [bar for bar in by_date[day] if 8 * 60 + 30 <= bar[0] < 9 * 60 + 30]
            if cash:
                cash_vol.append(float(sum(bar[5] for bar in cash)))
                cash_range.append(float(max(bar[2] for bar in cash) - min(bar[3] for bar in cash)))
        if day.weekday() != 6:
            continue
        sunday_bars = [bar for bar in by_date[day] if bar[0] >= OPEN]
        if not sunday_bars:
            continue
        contract = sunday_bars[0][6]
        sunday_bars = [bar for bar in sunday_bars if bar[6] == contract]
        later = [item for item in dates if item > day]
        next_day = later[0] if later else None
        morning = []
        if next_day is not None:
            morning = [bar for bar in by_date[next_day] if bar[0] <= MORNING_FLAT and bar[6] == contract]
        rows = sunday_bars + morning
        prior_days = [item for item in dates if item < day and item in friday_close]
        prior = prior_days[-1] if prior_days else None
        rolled = False
        close_px = None
        if prior is not None:
            close_px, prior_contract = friday_close[prior]
            rolled = prior_contract != contract
        minutes = np.asarray([bar[0] for bar in rows], dtype=np.int16)
        episode = Episode(
            sunday=day,
            trade_date=cash_session(day.year, day.month, day.day, OPEN),
            next_open=next_day,
            friday_close=None if rolled else close_px,
            friday_date=prior,
            contract=contract,
            rolled=rolled,
            minute=minutes,
            o=np.asarray([bar[1] for bar in rows], dtype=float),
            h=np.asarray([bar[2] for bar in rows], dtype=float),
            l=np.asarray([bar[3] for bar in rows], dtype=float),
            c=np.asarray([bar[4] for bar in rows], dtype=float),
            v=np.asarray([bar[5] for bar in rows], dtype=float),
        )
        episodes.append(episode)
        first_hour = [bar for bar in sunday_bars if bar[0] < HOUR]
        if first_hour:
            sunday_vol.append(float(sum(bar[5] for bar in first_hour)))
            sunday_range.append(float(max(bar[2] for bar in first_hour) - min(bar[3] for bar in first_hour)))
    volume = {
        "sunday_17_18_median_volume": _median(sunday_vol),
        "weekday_17_18_median_volume": _median(weekday_evening_vol),
        "cash_0830_0930_median_volume": _median(cash_vol),
        "sunday_first_hour_median_range": _median(sunday_range),
        "cash_first_hour_median_range": _median(cash_range),
        "sunday_n": len(sunday_vol),
        "weekday_evening_n": len(weekday_evening_vol),
        "cash_n": len(cash_vol),
    }
    return episodes, volume


def describe_gaps(episodes: list[Episode], tick: float) -> dict:
    usable = [ep for ep in episodes if ep.gap is not None and abs(ep.gap) >= MIN_GAP_TICKS * tick]
    gaps = np.asarray([ep.gap for ep in usable], dtype=float)
    pct = np.asarray([ep.gap / ep.friday_close for ep in usable if ep.friday_close], dtype=float)
    horizons = {}
    for name, limit in (("1h", 60), ("2h", 120), ("4h", 240), ("monday_0830", None)):
        hits = 0
        for ep in usable:
            if _filled(ep, limit):
                hits += 1
        horizons[name] = {"filled": hits, "n": len(usable), "rate": (hits / len(usable)) if usable else None}
    return {
        "episodes": len(episodes),
        "rolled": sum(1 for ep in episodes if ep.rolled),
        "no_friday": sum(1 for ep in episodes if ep.friday_date is None),
        "gaps": len(usable),
        "median_points": float(np.median(gaps)) if len(gaps) else None,
        "mean_points": float(gaps.mean()) if len(gaps) else None,
        "p10_points": float(np.quantile(gaps, 0.10)) if len(gaps) else None,
        "p90_points": float(np.quantile(gaps, 0.90)) if len(gaps) else None,
        "median_abs_points": float(np.median(np.abs(gaps))) if len(gaps) else None,
        "median_abs_percent": float(np.median(np.abs(pct))) if len(pct) else None,
        "up_share": float(np.mean(gaps > 0)) if len(gaps) else None,
        "fill": horizons,
        "abs_percent": [float(value) for value in np.abs(pct)],
    }


def _filled(ep: Episode, minutes: int | None) -> bool:
    gap = ep.gap
    if gap is None or not ep.friday_close:
        return False
    target = float(ep.friday_close)
    start = int(ep.minute[0])
    for minute, high, low in zip(ep.minute.tolist(), ep.h.tolist(), ep.l.tolist()):
        if minutes is None:
            if minute > MORNING_FLAT and int(minute) < OPEN:
                break
        elif minute >= OPEN and minute - start > minutes:
            break
        if gap > 0 and low <= target + 1e-9:
            return True
        if gap < 0 and high >= target - 1e-9:
            return True
    return False


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(np.median(np.asarray(values, dtype=float)))


def _day(ep: Episode) -> Day:
    n = len(ep.o)
    return Day(
        session=ep.trade_date,
        minute=ep.minute,
        o=ep.o,
        h=ep.h,
        l=ep.l,
        c=ep.c,
        v=ep.v,
        vwap=ep.c,
        atr=np.zeros(n),
        ema=np.zeros(n),
        five_end=np.zeros(n, dtype=np.int64),
        or_high={},
        or_low={},
        rth_i=0,
        flat_i=n - 1,
        exit_on="close",
        pdh=float("nan"),
        pdl=float("nan"),
        pdc=float(ep.friday_close) if ep.friday_close is not None else float("nan"),
        onh=float("nan"),
        onl=float("nan"),
        gap=float(ep.gap) if ep.gap is not None else float("nan"),
        rth_open=float(ep.o[0]),
        contract=0,
        name=ep.contract,
    )


def _index_at(ep: Episode, clock: int, after: int = 0) -> int | None:
    hits = np.flatnonzero((ep.minute >= clock) & (np.arange(len(ep.minute)) >= after))
    if len(hits) == 0:
        return None
    # Monday morning minutes are small. An evening clock must not match them.
    if clock >= OPEN:
        evening = [int(i) for i in hits if int(ep.minute[int(i)]) >= OPEN]
        return evening[0] if evening else None
    morning = [int(i) for i in hits if int(ep.minute[int(i)]) < OPEN]
    return morning[0] if morning else None


def run_rule(episodes: list[Episode], name: str, instrument) -> list[Trade]:
    trades: list[Trade] = []
    tick = instrument.tick
    for ep in episodes:
        if name in ("sun_gap_fade", "sun_gap_go"):
            intent = _gap_intent(ep, name, tick)
        elif name == "sun_orb30":
            intent = _orb_intent(ep, tick)
        else:
            intent = _fade_intent(ep, tick)
        if intent is None:
            continue
        day = _day(ep)
        with using_instrument(instrument):
            bracket = _try_market(
                day,
                intent["side"],
                intent["entry"],
                intent["stop"],
                intent["target"],
                intent["last"],
                intent["exit_on"],
            )
            if bracket:
                trades.extend(execute(day, [bracket], name))
    return trades


def _gap_intent(ep: Episode, name: str, tick: float) -> dict | None:
    gap = ep.gap
    if gap is None or abs(gap) < MIN_GAP_TICKS * tick or len(ep.o) < 2:
        return None
    entry = 1
    last = _index_at(ep, MORNING_FLAT)
    if last is None or last <= entry:
        return None
    ref = float(ep.o[entry])
    friday = float(ep.friday_close)
    width = abs(float(ep.o[0] - friday))
    if name == "sun_gap_fade":
        side = "short" if gap > 0 else "long"
        stop = ref + width if side == "short" else ref - width
        target = friday
    else:
        side = "long" if gap > 0 else "short"
        stop = friday
        target = ref + width if side == "long" else ref - width
    return {"side": side, "entry": entry, "stop": stop, "target": target, "last": last, "exit_on": "open"}


def _orb_intent(ep: Episode, tick: float) -> dict | None:
    window = np.flatnonzero((ep.minute >= OPEN) & (ep.minute < ORB_END))
    if len(window) < 2:
        return None
    hi = float(np.max(ep.h[window]))
    lo = float(np.min(ep.l[window]))
    if hi - lo < 2 * tick:
        return None
    start = int(window[-1]) + 1
    last = _index_at(ep, EVENING_FLAT, after=start)
    if last is None:
        return None
    for index in range(start, last):
        if int(ep.minute[index]) < OPEN:
            return None
        close = float(ep.c[index])
        if close > hi:
            side, stop = "long", lo
        elif close < lo:
            side, stop = "short", hi
        else:
            continue
        entry = index + 1
        if entry >= last:
            return None
        ref = float(ep.o[entry])
        distance = (ref - stop) if side == "long" else (stop - ref)
        if distance <= tick:
            return None
        target = ref + distance if side == "long" else ref - distance
        return {"side": side, "entry": entry, "stop": stop, "target": target, "last": last, "exit_on": "open"}
    return None


def _fade_intent(ep: Episode, tick: float) -> dict | None:
    window = np.flatnonzero((ep.minute >= OPEN) & (ep.minute < HOUR))
    if len(window) < 2:
        return None
    hi = float(np.max(ep.h[window]))
    lo = float(np.min(ep.l[window]))
    if hi - lo < 4 * tick:
        return None
    close = float(ep.c[int(window[-1])])
    span = hi - lo
    if close >= lo + (1 - QUARTER) * span:
        side, stop = "short", hi
    elif close <= lo + QUARTER * span:
        side, stop = "long", lo
    else:
        return None
    entry = _index_at(ep, HOUR, after=int(window[-1]))
    last = _index_at(ep, EVENING_FLAT, after=entry or 0)
    if entry is None or last is None or last <= entry:
        return None
    return {"side": side, "entry": entry, "stop": stop, "target": float(ep.o[0]), "last": last, "exit_on": "open"}


def sunday_candidate(train: dict, val: dict) -> bool:
    if train["trades"] < 30 or train["expectancy"] <= 0 or train["net"] <= 0:
        return False
    if val["trades"] < 15 or val["expectancy"] <= 0 or val["net"] <= 0:
        return False
    if train["expectancy"] > 0 and val["expectancy"] < 0.25 * train["expectancy"]:
        return False
    return True


def score_sunday(episodes: list[Episode], instrument) -> dict:
    groups = {"train": [], "validation": [], "holdout": []}
    for ep in episodes:
        groups[split_name(ep.trade_date)].append(ep)
    out = {}
    for name in RULES:
        scored = {}
        trades = {}
        for label in ("train", "validation"):
            trades[label] = run_rule(groups[label], name, instrument)
            scored[label] = summarize(trades[label])
        ok = sunday_candidate(scored["train"], scored["validation"])
        if ok:
            trades["holdout"] = run_rule(groups["holdout"], name, instrument)
            scored["holdout"] = summarize(trades["holdout"])
            sessions = [ep.trade_date for ep in groups["validation"]]
            daily = daily_vector(trades["validation"], sessions)
            raw = bootstrap_p(daily)
            flipped = signflip_p(daily)
            pvalue = {
                "bootstrap": raw,
                "bootstrap_adjusted": adjust(raw, N_SUNDAY),
                "signflip": flipped,
                "signflip_adjusted": adjust(flipped, N_SUNDAY),
            }
        else:
            scored["holdout"] = None
            pvalue = None
        out[name] = {"candidate": ok, "pvalue": pvalue, "stats": scored}
    return out


def public_gaps(summary: dict) -> dict:
    kept = dict(summary)
    kept.pop("abs_percent", None)
    return kept

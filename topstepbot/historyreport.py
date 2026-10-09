"""Run the live strategy on a 1-minute CSV and write a plain-English report.

The engine, the paper fills, and config/settings.yaml stay as they are.
Filter switches exist only inside this report. They are not saved.
"""

from __future__ import annotations

import csv
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from topstepbot.backtest.report import _max_drawdown, _mll_days
from topstepbot.bars import BarAggregator, bar_close_time
from topstepbot.broker.paper import PaperBroker
from topstepbot.config import BotConfig
from topstepbot.journal import Journal
from topstepbot.levels import opening_range_from_bars
from topstepbot.models import Bar, ClosedTrade
from topstepbot.strategy.engine import RuleStudy, SessionResult, StrategyEngine
from topstepbot.timeutil import CHICAGO, session_date

# Topstep 50K Combine figures this report compares against. They are not
# settings. The bot's own daily stop remains config risk.daily_max_loss.
COMBINE_PROFIT_TARGET = 3000.0
COMBINE_DLL = 1000.0
CONSISTENCY_CAP = 0.40
# Predeclared what-if. Not tuned on the file, and not written into the config.
RETEST_WHAT_IF_TICKS = 8
_CODE_MONTH = {"H": 3, "M": 6, "U": 9, "Z": 12}
_CONTRACT_RE = re.compile(r"(?:^MES\.|^MES)([HMUZ])(\d{1,2})$", re.IGNORECASE)


@dataclass(frozen=True)
class _LabeledBar:
    bar: Bar
    contract: str


def write_history_report(
    config: BotConfig,
    csv_path: str | Path,
    *,
    out: str | Path | None = None,
    label: str = "",
    log_dir: str | Path | None = None,
) -> str:
    """Replay the CSV and return the report. Also writes it when `out` is set."""
    path = Path(csv_path)
    labeled = load_labeled_bars(path, config.session.timezone)
    if not labeled:
        raise SystemExit(f"{path} has no bars.")
    bars, stitch_note = stitch_front_month(labeled)
    if not label and any("ES=F" in item.contract.upper() for item in labeled):
        label = (
            "PRELIMINARY. These are Yahoo Finance ES=F prices, valued with MES dollars "
            "($5 per point). This is not the Topstep Combine feed and it is not 6 to 12 months of MES."
        )
    logs = Path(log_dir) if log_dir is not None else Path(config.runtime.log_dir)
    baseline_study = RuleStudy(audit=True, audit_retest_ticks=RETEST_WHAT_IF_TICKS)
    baseline = _replay(config, bars, baseline_study, logs)
    text = _render(config, bars, baseline, baseline_study, stitch_note, label, logs)
    if out is not None:
        destination = Path(out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text + "\n", encoding="utf-8")
    return text


def load_labeled_bars(path: str | Path, tz_name: str = "America/Chicago") -> list[_LabeledBar]:
    tz = ZoneInfo(tz_name)
    rows: list[_LabeledBar] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    if reader.fieldnames is None:
        return []
    for raw in reader:
        lowered = {(key or "").strip().lower(): value for key, value in raw.items()}
        if not lowered.get("timestamp"):
            continue
        moment = _parse_time(lowered["timestamp"], tz)
        rows.append(
            _LabeledBar(
                Bar(
                    time=moment,
                    open=float(lowered["open"]),
                    high=float(lowered["high"]),
                    low=float(lowered["low"]),
                    close=float(lowered["close"]),
                    volume=float(lowered["volume"]),
                ),
                (lowered.get("contract") or "").strip(),
            )
        )
    rows.sort(key=lambda item: item.bar.time)
    return rows


def parse_contract_expiry(name: str, hint_year: int) -> date | None:
    """Month-code expiry, using the 15th as a stand-in for the real roll day."""
    match = _CONTRACT_RE.search(name.strip().upper())
    if match is None:
        dotted = name.strip().upper().split(".")
        if len(dotted) >= 2:
            match = _CONTRACT_RE.search("MES" + dotted[-1])
    if match is None:
        return None
    month = _CODE_MONTH[match.group(1).upper()]
    digits = match.group(2)
    if len(digits) == 2:
        year = 2000 + int(digits)
    else:
        digit = int(digits)
        decade = (hint_year // 10) * 10
        options = [decade - 10 + digit, decade + digit, decade + 10 + digit]
        year = min(options, key=lambda item: (abs(item - hint_year), -item))
    return date(year, month, 15)


def stitch_front_month(rows: list[_LabeledBar]) -> tuple[list[Bar], str]:
    """One series. Overlapping months keep the front contract (soonest expiry still ahead)."""
    if not rows:
        return [], "No bars."
    names = []
    seen = set()
    for item in rows:
        if item.contract not in seen:
            seen.add(item.contract)
            names.append(item.contract)
    hint = rows[0].bar.time.astimezone(CHICAGO).year
    expiries = {name: parse_contract_expiry(name, hint) for name in names}
    if len(names) == 1:
        bars = _dedupe_minutes(rows)
        label = names[0] or "unlabeled"
        return bars, f"One contract in the file: {label}. No roll."
    if all(expiry is None for expiry in expiries.values()):
        bars = _dedupe_minutes(rows)
        shown = ", ".join(name or "unlabeled" for name in names)
        return bars, (
            f"Could not read a quarter code from the contract names ({shown}). "
            "The bars were kept in time order as one series. A same-minute overlap keeps one bar."
        )
    by_minute: dict[datetime, list[_LabeledBar]] = {}
    for item in rows:
        minute = item.bar.time.astimezone(CHICAGO).replace(second=0, microsecond=0)
        by_minute.setdefault(minute, []).append(item)
    chosen: list[tuple[Bar, str]] = []
    for minute in sorted(by_minute):
        group = by_minute[minute]
        bar_day = minute.date()
        dated = [(expiries[item.contract], item) for item in group if expiries[item.contract] is not None]
        undated = [item for item in group if expiries[item.contract] is None]
        ahead = [(expiry, item) for expiry, item in dated if expiry is not None and expiry >= bar_day]
        if ahead:
            pick = min(ahead, key=lambda pair: pair[0])[1]
        elif dated:
            pick = max(dated, key=lambda pair: pair[0])[1]
        else:
            pick = undated[0]
        chosen.append((pick.bar, pick.contract))
    bars = [bar for bar, _name in chosen]
    rolls = _roll_notes(chosen)
    used = ", ".join(name or "unlabeled" for name in names)
    note = (
        f"Joined {len(names)} contracts by front month, using the 15th of the contract month "
        f"as the expiry stand-in ({used}). "
    )
    note += rolls or "The front month did not change inside the file."
    return bars, note


def _dedupe_minutes(rows: list[_LabeledBar]) -> list[Bar]:
    kept: dict[datetime, Bar] = {}
    for item in rows:
        minute = item.bar.time.astimezone(CHICAGO).replace(second=0, microsecond=0)
        kept[minute] = item.bar
    return [kept[key] for key in sorted(kept)]


def _roll_notes(chosen: list[tuple[Bar, str]]) -> str:
    parts = []
    previous_name = ""
    previous_close = 0.0
    for bar, name in chosen:
        if previous_name and name != previous_name:
            gap = bar.open - previous_close
            parts.append(
                f"Roll {previous_name} to {name} at {bar.time.isoformat()} "
                f"(next open minus previous close is {gap:+.2f} points)."
            )
        previous_name = name
        previous_close = bar.close
    if not parts:
        return ""
    return " ".join(parts)


def _replay(config: BotConfig, bars: list[Bar], study: RuleStudy | None, log_dir: Path) -> SessionResult:
    journal = Journal(log_dir, clock=bars[0].time)
    journal.logger.handlers = [
        handler for handler in journal.logger.handlers if not isinstance(handler, logging.StreamHandler)
    ]
    broker = PaperBroker(config)
    engine = StrategyEngine(config, broker, journal, armed=True)
    engine.study = study
    for bar in bars:
        engine.on_minute(bar)
    return engine.finish()


def _render(
    config: BotConfig,
    bars: list[Bar],
    result: SessionResult,
    study: RuleStudy,
    stitch_note: str,
    label: str,
    log_dir: Path,
) -> str:
    lines: list[str] = ["History report"]
    if label:
        lines.append(label)
    lines.append(
        "This report runs the same strategy the live bot uses. It does not change config/settings.yaml."
    )
    first = bars[0].time.astimezone(CHICAGO)
    last = bars[-1].time.astimezone(CHICAGO)
    days = _session_days(bars, config)
    lines.append(
        f"{len(bars)} one-minute bars, {first.isoformat()} through {last.isoformat()}, "
        f"{len(days)} cash sessions."
    )
    lines.append(stitch_note)
    lines.append(_cost_line(config))
    lines.append("")
    lines.append("Full sample")
    lines.extend(_performance_lines(config, result, result.trades))
    lines.append("")
    lines.append("Per day")
    lines.extend(_per_day(result))
    cut = max(1, (len(days) * 2) // 3) if days else 0
    in_days = set(days[:cut])
    out_days = set(days[cut:])
    lines.append("")
    lines.append(
        f"In-sample is the first {len(in_days)} of {len(days)} cash sessions. "
        "Out-of-sample is the rest. The rules are not refit. Indicators stay warm across the split "
        "because both halves come from one replay."
    )
    lines.append("In-sample")
    lines.extend(_slice_lines(config, result, in_days))
    lines.append("Out-of-sample")
    if not out_days:
        lines.append("Out-of-sample is empty. The file does not have a last third of sessions.")
    else:
        lines.extend(_slice_lines(config, result, out_days))
    lines.append("")
    lines.append("Filter impact")
    lines.append(
        "Counts are from the live rules. Chop, VWAP and the 21 EMA, the ATR stretch, and volume "
        "are counted on opening-range closes. A bar can be choppy and also fail another rule. "
        "VWAP, ATR, and volume use the first reason the live engine returns, in that order. "
        "The retest, fakeout, and 10:15 cutoff counts are the bars where that rule actually stopped a decision. "
        "The dollar figure is from a second replay with only that one rule relaxed. "
        "Removing the rule can also erase a baseline trade, because the day then takes a different path. "
        "Those erased trades are counted on the line and are not inside the blocked-trade dollars."
    )
    filter_lines, flagged = _filter_lines(config, bars, result, study, in_days, out_days, log_dir)
    lines.extend(filter_lines)
    lines.append("")
    lines.extend(_morning_lines(config, bars))
    lines.append("")
    lines.extend(_variant_lines(config, bars, result, in_days, out_days, days, log_dir))
    lines.append("")
    lines.append(
        "Recommendation: leave the live defaults as they are. "
        "A filter is only flagged when relaxing it adds trades in both halves and those "
        "extra out-of-sample trades make money. That is a note for a later test, not a change to make now."
    )
    if flagged:
        lines.append("Flagged for a later look: " + ", ".join(flagged) + ".")
    else:
        lines.append("No filter met that bar on this file.")
    lines.append("Live defaults were not changed.")
    return "\n".join(lines)


def _cost_line(config: BotConfig) -> str:
    tick = config.instrument.tick_size
    value = config.instrument.tick_value
    slip = config.risk.slippage_ticks
    fee = config.risk.round_turn_fee_per_contract
    round_turn = slip * 2 * value
    return (
        f"Costs: {slip} tick of slippage against the trader on the entry and again on the exit "
        f"({tick:g} points, ${value:,.2f} per tick, ${round_turn:,.2f} round turn per contract) "
        f"plus the config round-turn fee of ${fee:,.2f} per contract. "
        "That fee is a placeholder from a public example, not a verified statement fee."
    )


def _performance_lines(config: BotConfig, result: SessionResult, trades: list[ClosedTrade]) -> list[str]:
    stats = _trade_stats(trades)
    curve_dd = _max_drawdown(result.equity_curve)
    mll_days = _mll_days(result.equity_curve, config)
    bot_stop = sorted(
        day for day, low in result.daily_mtm_low.items() if low <= -config.risk.daily_max_loss
    )
    dll_days = sorted(day for day, low in result.daily_mtm_low.items() if low <= -COMBINE_DLL)
    target = _sessions_to_target(config, result)
    lines = [
        f"Trades: {stats['trades']}",
        f"Win rate: {stats['win_rate']:.1f}%",
        f"Average win: {stats['avg_win']}",
        f"Average loss: {stats['avg_loss']}",
        f"Expectancy per trade: {stats['expectancy']}",
        f"Net P&L: {_money(stats['net'])}",
        f"Max drawdown: ${curve_dd:,.2f}",
        (
            f"Days the bot's ${config.risk.daily_max_loss:,.0f} stop was reached: {_dates(bot_stop)}. "
            f"Days the simulated mark-to-market low was ${COMBINE_DLL:,.0f} or worse "
            f"(Topstep daily loss limit): {_dates(dll_days)}. "
            "The simulation flattens at the bot's own stop, so a day often never reaches the Topstep line."
        ),
        (
            f"Days that touched the ${config.risk.topstep_max_loss:,.0f} trailing maximum loss: "
            f"{_dates(mll_days)}."
        ),
        target,
        _consistency(result, stats["net"]),
    ]
    return lines


def _slice_lines(config: BotConfig, result: SessionResult, days: set[date]) -> list[str]:
    trades = [trade for trade in result.trades if trade.session_date in days]
    stats = _trade_stats(trades)
    curve = [
        (moment, equity)
        for moment, equity in result.equity_curve
        if session_date(moment, config.session) in days
    ]
    return [
        f"Trades: {stats['trades']}",
        f"Win rate: {stats['win_rate']:.1f}%",
        f"Average win: {stats['avg_win']}",
        f"Average loss: {stats['avg_loss']}",
        f"Expectancy per trade: {stats['expectancy']}",
        f"Net P&L: {_money(stats['net'])}",
        f"Max drawdown: ${_max_drawdown(curve):,.2f}",
    ]


def _per_day(result: SessionResult) -> list[str]:
    counts = Counter(trade.session_date for trade in result.trades)
    days = sorted(set(result.daily_pnl) | set(counts))
    if not days:
        return ["No completed session."]
    lines = []
    for day in days:
        pnl = result.daily_pnl.get(day, 0.0)
        lines.append(f"{day.isoformat()}: {_noun(counts.get(day, 0), 'trade')}, {_money(pnl)}")
    return lines


def _trade_stats(trades: list[ClosedTrade]) -> dict:
    wins = [trade.pnl for trade in trades if trade.pnl > 0]
    losses = [trade.pnl for trade in trades if trade.pnl < 0]
    net = sum(trade.pnl for trade in trades)
    count = len(trades)
    return {
        "trades": count,
        "win_rate": (100.0 * len(wins) / count) if count else 0.0,
        "avg_win": _money(sum(wins) / len(wins)) if wins else "no winning trades",
        "avg_loss": _money(sum(losses) / len(losses)) if losses else "no losing trades",
        "expectancy": _money(net / count) if count else "no trades",
        "net": net,
    }


def _sessions_to_target(config: BotConfig, result: SessionResult) -> str:
    goal = config.account.starting_balance + COMBINE_PROFIT_TARGET
    seen: list[date] = []
    for moment, equity in result.equity_curve:
        day = session_date(moment, config.session)
        if not seen or seen[-1] != day:
            seen.append(day)
        if equity >= goal:
            return (
                f"Cash sessions to reach the ${COMBINE_PROFIT_TARGET:,.0f} profit target: "
                f"{len(seen)} (first touch on {day.isoformat()})."
            )
    return (
        f"Cash sessions to reach the ${COMBINE_PROFIT_TARGET:,.0f} profit target: not reached."
    )


def _consistency(result: SessionResult, net: float) -> str:
    winning = {day: pnl for day, pnl in result.daily_pnl.items() if pnl > 0}
    if net <= 0:
        return (
            "Best-day share of total profit for the 40% consistency rule: "
            "total profit is not positive, so the rule has no passing total."
        )
    if not winning:
        return "Best-day share of total profit for the 40% consistency rule: no winning day."
    day = max(winning, key=winning.get)
    share = winning[day] / net
    relation = "inside" if share <= CONSISTENCY_CAP + 1e-9 else "outside"
    return (
        f"Best day {day.isoformat()} made ${winning[day]:,.2f}, which is {share * 100:.1f}% of "
        f"total profit ${net:,.2f}. That is {relation} the 40% consistency rule."
    )


def _filter_lines(
    config: BotConfig,
    bars: list[Bar],
    baseline: SessionResult,
    study: RuleStudy,
    in_days: set[date],
    out_days: set[date],
    log_dir: Path,
) -> tuple[list[str], list[str]]:
    counts = Counter(item["filter"] for item in study.blocks)
    entry_end = config.session.entry_end.strftime("%H:%M")
    wider_entry = _minute_before(config.session.flatten_time)
    specs = [
        (
            "chop",
            "Chop (EMA crosses or a flat slope)",
            RuleStudy(ignore_chop=True),
        ),
        (
            "vwap_ema",
            "VWAP and the 21 EMA",
            RuleStudy(ignore_vwap_ema=True),
        ),
        (
            "atr",
            f"ATR stretch (close more than {config.filters.overextended_atr:g} ATR from the 21 EMA)",
            RuleStudy(ignore_atr=True),
        ),
        (
            "volume",
            f"Volume at least {config.filters.volume_multiple:g} times the prior bars",
            RuleStudy(ignore_volume=True),
        ),
        (
            "retest",
            f"Retest within {config.exits.retest_tolerance_ticks} ticks "
            f"(the what-if widens this to {RETEST_WHAT_IF_TICKS} ticks, which is not a tuned number)",
            RuleStudy(retest_tolerance_ticks=RETEST_WHAT_IF_TICKS),
        ),
        (
            "fakeout",
            f"Fakeout rule (close back through the level within {config.exits.fakeout_bars} bars)",
            RuleStudy(ignore_fakeout=True),
        ),
        (
            "cutoff",
            f"{entry_end} cutoff (the what-if allows a new entry until {wider_entry.strftime('%H:%M')}, "
            "the minute before the flatten)",
            RuleStudy(entry_end=wider_entry),
        ),
    ]
    lines = []
    flagged: list[str] = []
    for key, title, probe in specs:
        probed = _replay(config, bars, probe, log_dir)
        extra, missing = _trade_diff(baseline.trades, probed.trades)
        extra_net = sum(trade.pnl for trade in extra)
        in_extra = [trade for trade in extra if trade.session_date in in_days]
        out_extra = [trade for trade in extra if trade.session_date in out_days]
        out_net = sum(trade.pnl for trade in out_extra)
        lines.append(
            f"{title}: blocked {counts.get(key, 0)} breakouts on the live-rule pass. "
            f"Relaxing only that rule added {_noun(len(extra), 'trade')} worth {_money(extra_net)}. "
            f"{_noun(len(missing), 'baseline trade')} disappeared on the other path."
        )
        if in_extra and out_extra and out_net > 0:
            flagged.append(title)
    return lines, flagged


def _variant_lines(
    config: BotConfig,
    bars: list[Bar],
    baseline: SessionResult,
    in_days: set[date],
    out_days: set[date],
    days: list[date],
    log_dir: Path,
) -> list[str]:
    """Five entry definitions fixed before looking at a file. Live settings stay put."""
    specs: list[tuple[str, RuleStudy | None]] = [
        ("(a) Current rules. A retest must come within 2 ticks, and the stop sits just beyond that retest bar.", None),
        (
            "(b) Same rules, but the retest may be 8 ticks away. Eight was chosen in advance. It was not fit to this file.",
            RuleStudy(retest_tolerance_ticks=RETEST_WHAT_IF_TICKS),
        ),
        (
            "(c) Same rules, but the retest may be as far as 0.25 times that day's opening-range width. "
            "The 0.25 was chosen in advance. It was not fit to this file.",
            RuleStudy(retest_or_fraction=0.25),
        ),
        (
            "(d) Setup A enters at the 5-minute breakout close and puts the stop at the opening-range midpoint. "
            "There is no retest. Setup B is off for this comparison.",
            RuleStudy(setup_a_close_entry=True),
        ),
        (
            "(e) Same 2-tick retest and the same stop-entry beyond the retest bar. "
            "The protective stop goes beyond the pullback swing (the deepest price of the whole pullback), "
            "plus the usual 2-tick buffer.",
            RuleStudy(retest_stop_beyond_swing=True),
        ),
    ]
    lines = [
        "Entry variants",
        (
            f"{len(days)} cash sessions is a small sample. These five definitions were written down "
            "before the results. Live trading stays on (a). Nothing here is saved to config/settings.yaml."
        ),
    ]
    for title, study in specs:
        result = baseline if study is None else _replay(config, bars, study, log_dir)
        lines.append(title)
        lines.extend(_variant_stats(config, result, in_days, out_days))
    return lines


def _variant_stats(
    config: BotConfig, result: SessionResult, in_days: set[date], out_days: set[date]
) -> list[str]:
    stats = _trade_stats(result.trades)
    worst = "no completed day"
    if result.daily_pnl:
        day = min(result.daily_pnl, key=result.daily_pnl.get)
        worst = f"{day.isoformat()} {_money(result.daily_pnl[day])} realized"
    bot_days = sorted(day for day, low in result.daily_mtm_low.items() if low <= -config.risk.daily_max_loss)
    dll_days = sorted(day for day, low in result.daily_mtm_low.items() if low <= -COMBINE_DLL)
    lows = list(result.daily_mtm_low.values())
    worst_low = min(lows) if lows else 0.0
    lines = [
        (
            f"Trades: {stats['trades']}. Win rate: {stats['win_rate']:.1f}%. "
            f"Expectancy per trade: {stats['expectancy']}. Net P&L: {_money(stats['net'])}. "
            f"Max drawdown: {_money(_max_drawdown(result.equity_curve))}."
        ),
        (
            f"Worst day: {worst}. Deepest intraday mark: {_money(worst_low)}. "
            f"Days that reached the ${config.risk.daily_max_loss:,.0f} bot stop: {_dates(bot_days)}. "
            f"Days that reached the ${COMBINE_DLL:,.0f} daily loss limit: {_dates(dll_days)}."
        ),
        "In-sample: " + _variant_slice(config, result, in_days),
        "Out-of-sample: " + (
            _variant_slice(config, result, out_days) if out_days else "no sessions in the last third."
        ),
    ]
    if result.skips:
        top = sorted(result.skips.items(), key=lambda item: item[1], reverse=True)[:3]
        lines.append("Most common skips: " + ", ".join(f"{name}={count}" for name, count in top))
    return lines


def _variant_slice(config: BotConfig, result: SessionResult, days: set[date]) -> str:
    trades = [trade for trade in result.trades if trade.session_date in days]
    stats = _trade_stats(trades)
    curve = [
        (moment, equity)
        for moment, equity in result.equity_curve
        if session_date(moment, config.session) in days
    ]
    return (
        f"{_noun(stats['trades'], 'trade')}, win rate {stats['win_rate']:.1f}%, "
        f"expectancy {stats['expectancy']}, net {_money(stats['net'])}, "
        f"max drawdown {_money(_max_drawdown(curve))}"
    )


def _trade_diff(
    baseline: list[ClosedTrade], other: list[ClosedTrade]
) -> tuple[list[ClosedTrade], list[ClosedTrade]]:
    base_keys = {_trade_key(trade) for trade in baseline}
    other_keys = {_trade_key(trade) for trade in other}
    extra = [trade for trade in other if _trade_key(trade) not in base_keys]
    missing = [trade for trade in baseline if _trade_key(trade) not in other_keys]
    return extra, missing


def _trade_key(trade: ClosedTrade) -> tuple:
    return (trade.session_date, trade.entry_time, trade.side, trade.setup, round(trade.entry_price, 2))


def _morning_lines(config: BotConfig, bars: list[Bar]) -> list[str]:
    session = config.session
    tick = config.instrument.tick_size
    by_day: dict[date, list[Bar]] = {}
    for bar in bars:
        by_day.setdefault(session_date(bar.time, session), []).append(bar)
    aggregator = BarAggregator(config.filters.signal_timeframe_minutes, session.timezone)
    signal: list[Bar] = []
    for bar in bars:
        finished = aggregator.add(bar)
        if finished is not None:
            signal.append(finished)
    last = aggregator.flush()
    if last is not None:
        signal.append(last)
    widths: list[float] = []
    up = 0
    down = 0
    with_range = 0
    up_held = 0
    down_held = 0
    depths: list[float] = []
    within_two = 0
    never_back = 0
    entry_start = _minutes(session.entry_start)
    flatten = _minutes(session.flatten_time)
    for day, day_bars in sorted(by_day.items()):
        opening = opening_range_from_bars(
            day_bars, day, session.timezone, session.rth_open, session.opening_range_end
        )
        if opening is None or tick <= 0:
            continue
        with_range += 1
        widths.append((opening.high - opening.low) / tick)
        day_signal = [bar for bar in signal if session_date(bar.time, session) == day]
        window = [
            bar
            for bar in day_signal
            if entry_start <= _minutes(bar.time.astimezone(ZoneInfo(session.timezone)).time()) < flatten
        ]
        if not window:
            continue
        for level, above in ((opening.high, True), (opening.low, False)):
            index = _first_break(window, level, above=above)
            if index is None:
                continue
            if above:
                up += 1
                if window[-1].close > level:
                    up_held += 1
            else:
                down += 1
                if window[-1].close < level:
                    down_held += 1
            later = window[index + 1 :]
            if not later:
                continue
            if above:
                depth = (level - min(bar.low for bar in later)) / tick
            else:
                depth = (max(bar.high for bar in later) - level) / tick
            if depth < 0:
                never_back += 1
                depths.append(0.0)
            else:
                depths.append(depth)
                if depth <= config.exits.retest_tolerance_ticks:
                    within_two += 1
    lines = ["How MES moves 8:30-10:30 CT"]
    lines.append(
        "The opening range is the 08:30-08:45 high and low. A break is a 5-minute close through that "
        "level from 08:45 until the flatten. Follow-through means the last 5-minute bar before the flatten "
        "is still through the level."
    )
    if not with_range:
        lines.append("No session in this file has an 08:30-08:45 opening range.")
        return lines
    lines.append(
        f"Typical opening-range width: {_median(widths):.1f} ticks "
        f"({_median(widths) * tick:.2f} points) across {with_range} sessions. "
        f"Average width {_mean(widths):.1f} ticks."
    )
    lines.append(
        f"Of those sessions, {up} closed a 5-minute bar above the opening-range high and "
        f"{down} closed one below the opening-range low."
    )
    lines.append(
        f"Follow-through into the flatten: {up_held} of {up} upside breaks were still above the high, "
        f"and {down_held} of {down} downside breaks were still below the low."
    )
    if depths:
        lines.append(
            f"Typical retest depth after the first 5-minute close through the level: "
            f"{_median(depths):.1f} ticks (median), {_mean(depths):.1f} ticks (average), "
            f"over {len(depths)} breaks that had a later bar. "
            f"{within_two} of those pulled back no more than {config.exits.retest_tolerance_ticks} ticks. "
            f"{never_back} never traded back to the level."
        )
    else:
        lines.append("No break had a later 5-minute bar before the flatten, so retest depth is not measured.")
    return lines


def _first_break(window: list[Bar], level: float, *, above: bool) -> int | None:
    for index, bar in enumerate(window):
        if above and bar.close > level:
            return index
        if not above and bar.close < level:
            return index
    return None


def _session_days(bars: list[Bar], config: BotConfig) -> list[date]:
    days: list[date] = []
    seen = set()
    for bar in bars:
        day = session_date(bar.time, config.session)
        if day not in seen:
            seen.add(day)
            days.append(day)
    return days


def _minute_before(clock: time) -> time:
    total = clock.hour * 60 + clock.minute - 1
    if total < 0:
        total = 0
    return time(total // 60, total % 60)


def _minutes(clock: time) -> int:
    return clock.hour * 60 + clock.minute


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _noun(count: int, word: str) -> str:
    if count == 1:
        return f"1 {word}"
    return f"{count} {word}s"


def _money(value: float) -> str:
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def _dates(days: list[date]) -> str:
    if not days:
        return "none"
    return ", ".join(day.isoformat() for day in days)


def _parse_time(text: str, tz: ZoneInfo) -> datetime:
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment

"""One engine for live, paper, and backtest. It only sees completed bars.

Orders are sent after a 5-minute bar closes and can fill on later 1-minute bars.
The engine never looks at a bar that has not closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from topstepbot.bars import BarAggregator, bar_close_time
from topstepbot.broker.base import Broker
from topstepbot.config import BotConfig
from topstepbot.indicators import (
    atr,
    ema,
    is_chop,
    is_retest_bar,
    is_strong_break,
    macd,
    round_to_tick,
    swing_points,
    true_ranges,
    volume_confirmed,
)
from topstepbot.journal import Journal
from topstepbot.levels import (
    build_level_map,
    next_target,
    opening_range_from_bars,
)
from topstepbot.models import Bar, BracketLeg, ClosedTrade, DayStats, Fill, OpeningRange, Side
from topstepbot.news import block_reason
from topstepbot.risk import evaluate_entry, initial_mll_floor, trail_mll_floor
from topstepbot.session import entries_allowed, must_flatten, setup_b_allowed
from topstepbot.sizing import contracts_for_risk, dollar_risk, split_quantity
from topstepbot.timeutil import as_chicago, at_clock, session_date


@dataclass
class RuleStudy:
    """Optional switches for the history report. Live trading leaves this unset.

    ``audit`` only records. The ignore flags are off unless a report asks
    what one rule costs. They do not change ``config/settings.yaml``.
    """

    audit: bool = False
    ignore_chop: bool = False
    ignore_vwap_ema: bool = False
    ignore_atr: bool = False
    ignore_volume: bool = False
    ignore_fakeout: bool = False
    retest_tolerance_ticks: int | None = None
    # Count a pending retest the live band rejects and this wider band would take.
    # Recording only. Entries still use retest_tolerance_ticks or the config.
    audit_retest_ticks: int | None = None
    entry_end: object | None = None
    blocks: list = field(default_factory=list)
    _seen: set = field(default_factory=set)


@dataclass
class PendingBreak:
    setup: str
    side: Side
    level_name: str
    level: float
    break_index: int
    done: bool = False


@dataclass
class LogicalTrade:
    setup: str
    side: Side
    level_name: str
    lyl: float
    planned_entry: float
    initial_stop: float
    r_distance: float
    initial_risk: float
    groups: list[str]
    t1_group: str
    qty: int
    filled_qty: int = 0
    entry_notional: float = 0.0
    entry_time: datetime | None = None
    signal_index: int = 0
    t1_done: bool = False
    placed_at: datetime | None = None


@dataclass
class SessionResult:
    trades: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    daily_pnl: dict[date, float] = field(default_factory=dict)
    daily_mtm_low: dict[date, float] = field(default_factory=dict)
    skips: dict[str, int] = field(default_factory=dict)
    mll_floor_end: float = 0.0
    ending_equity: float = 0.0


class StrategyEngine:
    def __init__(
        self,
        config: BotConfig,
        broker: Broker,
        journal: Journal,
        *,
        armed: bool = True,
    ) -> None:
        self.config = config
        self.broker = broker
        self.journal = journal
        self.armed = armed
        self.tz = ZoneInfo(config.session.timezone)
        self.minutes: list[Bar] = []
        self.bars_5: list[Bar] = []
        self.aggregator = BarAggregator(config.filters.signal_timeframe_minutes, config.session.timezone)
        self.day = DayStats()
        self.session_day: date | None = None
        self.session_groups: list[str] = []
        self.cumulative_prior = 0.0
        self.mll_floor = initial_mll_floor(config.account.starting_balance, config.risk.topstep_max_loss)
        self.pending: list[PendingBreak] = []
        self.trade: LogicalTrade | None = None
        self.closed: list[ClosedTrade] = []
        self.opening: OpeningRange | None = None
        self.opening_frozen = False
        self.levels = []
        self.vwap: float | None = None
        self._vwap_pv = 0.0
        self._vwap_vol = 0.0
        self._vwap_key: date | None = None
        self.day_flattened = False
        self.last_order_at: datetime | None = None
        self.cooldown_until: dict[Side, int] = {Side.LONG: -1, Side.SHORT: -1}
        self.skips: dict[str, int] = {}
        self.last_watch = ""
        self.equity_curve: list[tuple[datetime, float]] = []
        # None on the live bot. The history report sets this to count filters.
        self.study: RuleStudy | None = None
        # Same EMA, ATR, and swings as recomputing the whole 5-minute list.
        # Updated one bar at a time so a long history report stays fast.
        self._ind_n = 0
        self._ema: list = []
        self._atr: list = []
        self._trs: list[float] = []
        self._swing_high: list = []
        self._swing_low: list = []
        # Running high/low for prior RTH, overnight, and London. Same windows
        # as the scans in levels.py, updated once per minute instead of
        # re-reading the whole history on every 5-minute bar.
        self._rth_stats: dict[date, list[float]] = {}
        self._overnight_stats: dict[date, list[float]] = {}
        self._london_stats: dict[date, list[float]] = {}
        self.daily_pnl: dict[date, float] = {}
        self.daily_mtm_low: dict[date, float] = {}
        self._plan_logged = False
        self._tag = 0

    def on_minute(self, bar: Bar) -> None:
        self._roll_session_if_needed(bar)
        self.minutes.append(bar)
        self._absorb_level_bar(bar)
        self._update_vwap(bar)
        self._freeze_opening_range(bar)

        if self.session_day is not None and must_flatten(bar.time, self.config.session) and not self.day_flattened:
            self._flatten(bar.open, bar.time, "session flatten")
            self.day_flattened = True
            self.day.halted = True
            self.day.halt_reason = "session flatten"
            self._mark(bar.close, bar.time)
            finished = self.aggregator.add(bar)
            if finished is not None:
                self.bars_5.append(finished)
            return

        if self.trade is not None and self.trade.filled_qty == 0:
            self._cancel_if_chasing(bar)

        fills = self.broker.on_bar(bar)
        self._consume(fills)
        self._sync_pnl(bar.close)
        self._enforce_daily_limits(bar.close, bar.time)
        self._mark(bar.close, bar.time)

        finished = self.aggregator.add(bar)
        if finished is not None:
            self.bars_5.append(finished)
            self._on_signal_bar(finished)

    def service(self, now: datetime, price: float) -> None:
        """Poll a live broker between 1-minute bars."""
        poll = getattr(self.broker, "poll", None)
        fills = poll(now) if poll else []
        if fills:
            self._consume(fills)
        self._sync_pnl(price)
        self._enforce_daily_limits(price, now)
        self._mark(price, now)

    def finish(self) -> SessionResult:
        last = self.aggregator.flush()
        if last is not None:
            self.bars_5.append(last)
            self._on_signal_bar(last)
        if self.minutes and self.broker.net_qty() != 0:
            last_bar = self.minutes[-1]
            self._flatten(last_bar.close, last_bar.time, "end of data")
        if self.session_day is not None:
            self.daily_pnl[self.session_day] = self.day.realized
        equity = self._equity()
        return SessionResult(
            trades=list(self.closed),
            equity_curve=list(self.equity_curve),
            daily_pnl=dict(self.daily_pnl),
            daily_mtm_low=dict(self.daily_mtm_low),
            skips=dict(self.skips),
            mll_floor_end=self.mll_floor,
            ending_equity=equity,
        )

    def _roll_session_if_needed(self, bar: Bar) -> None:
        day = session_date(bar.time, self.config.session)
        if self.session_day is None:
            self.session_day = day
            self._rebuild_levels(bar.close)
            return
        if day == self.session_day:
            return
        if self.broker.net_qty() != 0 or self.broker.working_entry_count():
            self._flatten(bar.open, bar.time, "session roll")
        self.daily_pnl[self.session_day] = self.day.realized
        equity = self.config.account.starting_balance + self.cumulative_prior + self.day.realized
        self.mll_floor = trail_mll_floor(self.mll_floor, equity, self.config.risk.topstep_max_loss)
        self.cumulative_prior += self.day.realized
        self.day = DayStats()
        self.session_groups = []
        self.pending = []
        self.trade = None
        self.opening = None
        self.opening_frozen = False
        self.day_flattened = False
        self.cooldown_until = {Side.LONG: -1, Side.SHORT: -1}
        self._plan_logged = False
        self.session_day = day
        self._rebuild_levels(bar.close)

    def _update_vwap(self, bar: Bar) -> None:
        local = as_chicago(bar.time, self.config.session.timezone)
        if local.time() >= self.config.session.vwap_anchor:
            key = local.date()
        else:
            key = local.date() - timedelta(days=1)
        if key != self._vwap_key:
            self._vwap_key = key
            self._vwap_pv = 0.0
            self._vwap_vol = 0.0
        typical = (bar.high + bar.low + bar.close) / 3.0
        self._vwap_vol += bar.volume
        self._vwap_pv += typical * bar.volume
        self.vwap = self._vwap_pv / self._vwap_vol if self._vwap_vol > 0 else None

    def _freeze_opening_range(self, bar: Bar) -> None:
        if self.opening_frozen or self.session_day is None:
            return
        local = as_chicago(bar.time, self.config.session.timezone)
        if local.date() != self.session_day:
            return
        if local.time() < self.config.session.opening_range_end:
            return
        found = opening_range_from_bars(
            self.minutes,
            self.session_day,
            self.config.session.timezone,
            self.config.session.rth_open,
            self.config.session.opening_range_end,
        )
        self.opening = found
        self.opening_frozen = True
        self._rebuild_levels(bar.close)
        if found is not None and not self._plan_logged:
            self._plan_logged = True
            self.journal.info(
                f"PLAN session={self.session_day} OR_high={found.high:.2f} OR_low={found.low:.2f} "
                f"vwap={self.vwap:.2f}" if self.vwap is not None
                else f"PLAN session={self.session_day} OR_high={found.high:.2f} OR_low={found.low:.2f}"
            )
            self.journal.info(
                f"PLAN above {found.high:.2f} look for long A/B; below {found.low:.2f} look for short A/B. "
                "You approve this by leaving the bot armed while you are at the PC."
            )

    def _rebuild_levels(self, near: float) -> None:
        if self.session_day is None:
            return
        self.levels = build_level_map(
            prior=self._prior_rth(self.session_day),
            overnight=self._pair(self._overnight_stats.get(self.session_day)),
            london=self._pair(self._london_stats.get(self.session_day)),
            opening=self.opening,
            vwap=self.vwap,
            round_step=self.config.filters.round_number_points,
            near_price=near,
        )

    def _absorb_level_bar(self, bar: Bar) -> None:
        """Fold one minute into the same windows prior_rth_hlc, overnight_hl, and london_hl use."""
        session = self.config.session
        local = as_chicago(bar.time, session.timezone)
        day = local.date()
        minute = local.hour * 60 + local.minute
        rth_open = session.rth_open.hour * 60 + session.rth_open.minute
        rth_end = session.prior_rth_end.hour * 60 + session.prior_rth_end.minute
        anchor = session.vwap_anchor.hour * 60 + session.vwap_anchor.minute
        if rth_open <= minute < rth_end:
            slot = self._rth_stats.get(day)
            if slot is None:
                self._rth_stats[day] = [bar.high, bar.low, bar.close]
            else:
                if bar.high > slot[0]:
                    slot[0] = bar.high
                if bar.low < slot[1]:
                    slot[1] = bar.low
                slot[2] = bar.close
        if minute >= anchor:
            self._bump_hl(self._overnight_stats, day + timedelta(days=1), bar)
        elif minute < rth_open:
            self._bump_hl(self._overnight_stats, day, bar)
        # levels.london_hl defaults to 02:00 CT through the cash open.
        if 2 * 60 <= minute < rth_open:
            self._bump_hl(self._london_stats, day, bar)

    def _prior_rth(self, session_day: date) -> tuple[float, float, float] | None:
        previous = session_day - timedelta(days=1)
        for _ in range(4):
            if previous.weekday() < 5:
                break
            previous -= timedelta(days=1)
        slot = self._rth_stats.get(previous)
        if slot is None:
            return None
        return (slot[0], slot[1], slot[2])

    @staticmethod
    def _bump_hl(store: dict[date, list[float]], key: date, bar: Bar) -> None:
        slot = store.get(key)
        if slot is None:
            store[key] = [bar.high, bar.low]
            return
        if bar.high > slot[0]:
            slot[0] = bar.high
        if bar.low < slot[1]:
            slot[1] = bar.low

    @staticmethod
    def _pair(slot: list[float] | None) -> tuple[float, float] | None:
        if slot is None:
            return None
        return (slot[0], slot[1])

    def _on_signal_bar(self, bar: Bar) -> None:
        if self.session_day is None:
            return
        self._rebuild_levels(bar.close)
        index = len(self.bars_5) - 1
        decision_time = bar_close_time(bar, self.config.filters.signal_timeframe_minutes)
        if self.trade is not None:
            self._manage_open_trade(bar, index, decision_time)
            if self.trade is not None and (self.trade.filled_qty > 0 or self._entries_still_working()):
                return
        if self.day_flattened or self.day.halted:
            return
        if self.study is not None and self.study.audit:
            self._audit_opening_break(bar)
        if not entries_allowed(decision_time, self.config.session):
            self._audit_cutoff(bar, decision_time)
        if not self._entries_open(decision_time):
            return
        news = block_reason(decision_time, self.config.news, self.config.session.timezone)
        if news:
            self._skip(news, bar)
            return
        if self.config.filters.require_index_agreement:
            self._skip("index agreement feed is not connected", bar)
            return
        if not self._indicators_ready():
            self._skip("indicators warming up", bar)
            return
        if self._chop():
            if not self._ignoring("ignore_chop"):
                self._skip("chop filter", bar)
                self.last_watch = "chop filter"
                return
        before = dict(self.skips)
        signal_side = self._arm_breaks(bar, index)
        if signal_side is None:
            if self.skips == before:
                self._narrate_quiet_bar(bar)
            return
        self._try_enter(signal_side, decision_time)

    def _arm_breaks(self, bar: Bar, index: int):
        """Update pending breaks. Returns a signal tuple or None."""
        armed = self._signal_from_pending(bar, index)
        if armed is not None:
            return armed
        filters = self.config.filters
        if filters.setup_a_enabled and self.opening is not None and self._bar_opens_in_entry_window(bar):
            for side, level_name, level in (
                (Side.LONG, "or_high", self.opening.high),
                (Side.SHORT, "or_low", self.opening.low),
            ):
                if self._slot_used("A", level_name, side):
                    continue
                if self._break_is_valid(bar, level, side):
                    if filters.setup_a_entry_mode == "classic":
                        built = self._build_classic(bar, side, level_name, level, index)
                        if built is not None:
                            self.pending.append(
                                PendingBreak("A", side, level_name, level, index, done=True)
                            )
                            return built
                    else:
                        self.pending.append(PendingBreak("A", side, level_name, level, index))
                        self.journal.signal(
                            setup="A",
                            side=side.value,
                            level=level_name,
                            price=f"{level:.2f}",
                            bar=bar.time.isoformat(),
                            state="break_waiting_retest",
                        )
        if (
            filters.setup_b_enabled
            and self._bar_opens_in_entry_window(bar)
            and setup_b_allowed(bar_close_time(bar, filters.signal_timeframe_minutes), self.config.session)
        ):
            for level_name in filters.setup_b_levels:
                level = self._level_price(level_name)
                if level is None:
                    continue
                for side in (Side.LONG, Side.SHORT):
                    if self._slot_used("B", level_name, side):
                        continue
                    if not self._trend_ok(side):
                        continue
                    if self._break_is_valid(bar, level, side):
                        self.pending.append(PendingBreak("B", side, level_name, level, index))
                        self.journal.signal(
                            setup="B",
                            side=side.value,
                            level=level_name,
                            price=f"{level:.2f}",
                            bar=bar.time.isoformat(),
                            state="break_waiting_retest",
                        )
        return None

    def _signal_from_pending(self, bar: Bar, index: int):
        order = {"A": 0, "B": 1}
        active = [item for item in self.pending if not item.done]
        active.sort(key=lambda item: (order.get(item.setup, 9), 0 if item.side is Side.LONG else 1))
        chosen = None
        for item in active:
            since = index - item.break_index
            if since < 1:
                continue
            if self._closed_back_through(bar, item) and since <= self.config.exits.fakeout_bars:
                self._audit_name("fakeout", bar, item.level_name)
                if not self._ignoring("ignore_fakeout"):
                    item.done = True
                    self._skip(f"fakeout {item.setup} {item.level_name} {item.side.value}", bar)
                    continue
            if since > self.config.exits.retest_timeout_bars:
                item.done = True
                self._skip(f"retest timeout {item.setup} {item.level_name}", bar)
                continue
            self._audit_retest_depth(bar, item)
            if not is_retest_bar(
                bar,
                item.level,
                item.side,
                self._retest_tolerance(),
            ):
                continue
            if self._side_blocked(item.side, index):
                item.done = True
                self._skip(f"cooldown {item.side.value}", bar)
                continue
            built = self._build_retest(bar, item, index)
            item.done = True
            if built is not None and chosen is None:
                chosen = built
        return chosen

    def _build_retest(self, bar: Bar, item: PendingBreak, index: int):
        tick = self.config.instrument.tick_size
        exits = self.config.exits
        if item.side is Side.LONG:
            entry = round_to_tick(bar.high + exits.entry_stop_offset_ticks * tick, tick)
            stop = round_to_tick(bar.low - exits.stop_buffer_ticks * tick, tick)
        else:
            entry = round_to_tick(bar.low - exits.entry_stop_offset_ticks * tick, tick)
            stop = round_to_tick(bar.high + exits.stop_buffer_ticks * tick, tick)
        return self._package(item.setup, item.side, item.level_name, item.level, entry, stop, bar, index, "stop")

    def _build_classic(self, bar: Bar, side: Side, level_name: str, level: float, index: int):
        if self.opening is None:
            return None
        tick = self.config.instrument.tick_size
        buffer = self.config.exits.stop_buffer_ticks * tick
        entry = round_to_tick(bar.close, tick)
        if side is Side.LONG:
            stop = round_to_tick(self.opening.low - buffer, tick)
        else:
            stop = round_to_tick(self.opening.high + buffer, tick)
        return self._package("A", side, level_name, level, entry, stop, bar, index, "market")

    def _package(
        self,
        setup: str,
        side: Side,
        level_name: str,
        level: float,
        entry: float,
        stop: float,
        bar: Bar,
        index: int,
        entry_type: str,
    ):
        risk_points = abs(entry - stop)
        if risk_points <= 0:
            self._skip("stop is not beyond the entry", bar)
            return None
        if side is Side.LONG and bar.close > level + self.config.exits.no_chase_r * risk_points:
            self._skip("no chase", bar)
            return None
        if side is Side.SHORT and bar.close < level - self.config.exits.no_chase_r * risk_points:
            self._skip("no chase", bar)
            return None
        target = next_target(
            self.levels,
            entry,
            stop,
            side is Side.LONG,
            self.config.exits.min_reward_risk,
            exclude_prices={level},
        )
        if target is None:
            self._skip("no key level at least 2R away", bar)
            return None
        runner = next_target(
            self.levels,
            target,
            stop,
            side is Side.LONG,
            0.0,
            exclude_prices={level, target},
        )
        self.journal.signal(
            setup=setup,
            side=side.value,
            level=level_name,
            lyl=f"{level:.2f}",
            entry=f"{entry:.2f}",
            stop=f"{stop:.2f}",
            target=f"{target:.2f}",
            bar=bar.time.isoformat(),
            entry_type=entry_type,
            state="signal",
        )
        return {
            "setup": setup,
            "side": side,
            "level_name": level_name,
            "level": level,
            "entry": entry,
            "stop": stop,
            "target": target,
            "runner": runner,
            "bar": bar,
            "index": index,
            "entry_type": entry_type,
        }

    def _try_enter(self, spec: dict, decision_time: datetime) -> None:
        if not self.armed:
            self._skip("bot is not armed", spec["bar"])
            return
        if self.last_order_at is not None:
            elapsed = (decision_time - self.last_order_at).total_seconds()
            if elapsed < self.config.compliance.min_seconds_between_orders:
                self._skip("minimum order interval", spec["bar"])
                return
        risk_dollars = self.config.risk.risk_per_trade
        equity = self._equity()
        preview = evaluate_entry(
            self.day,
            new_trade_risk=risk_dollars,
            risk=self.config.risk,
            equity=equity,
            mll_floor=self.mll_floor,
        )
        if not preview.allowed:
            self._skip(preview.reason, spec["bar"])
            return
        risk_dollars *= preview.risk_multiplier
        qty = contracts_for_risk(
            entry=spec["entry"],
            stop=spec["stop"],
            tick_size=self.config.instrument.tick_size,
            tick_value=self.config.instrument.tick_value,
            risk_per_trade=risk_dollars,
            slippage_ticks=self.config.risk.slippage_ticks,
            round_turn_fee=self.config.risk.round_turn_fee_per_contract,
            max_contracts=self.config.risk.max_contracts,
            topstep_max_contracts=self.config.risk.topstep_max_contracts,
        )
        if qty < 1:
            self._skip("stop too wide for the risk budget", spec["bar"])
            return
        trade_risk = dollar_risk(
            qty,
            spec["entry"],
            spec["stop"],
            self.config.instrument.tick_size,
            self.config.instrument.tick_value,
            self.config.risk.slippage_ticks,
            self.config.risk.round_turn_fee_per_contract,
        )
        budget = evaluate_entry(
            self.day,
            new_trade_risk=trade_risk,
            risk=self.config.risk,
            equity=equity,
            mll_floor=self.mll_floor,
        )
        if not budget.allowed:
            self._skip(budget.reason, spec["bar"])
            return
        first_qty, runner_qty = split_quantity(qty, self.config.exits.partial_fraction)
        legs: list[BracketLeg] = []
        legs.append(self._leg(spec, first_qty, spec["target"], "t1"))
        if runner_qty > 0:
            legs.append(self._leg(spec, runner_qty, spec["runner"], "runner"))
        group_ids = self.broker.place_brackets(legs)
        self.session_groups.extend(group_ids)
        self.last_order_at = decision_time
        for leg, group_id in zip(legs, group_ids):
            self.journal.order(
                group=group_id,
                tag=leg.tag,
                side=leg.side.value,
                qty=leg.qty,
                entry_type=leg.entry_type,
                entry=leg.entry_price,
                stop=leg.stop_price,
                target=leg.target_price,
                setup=leg.setup,
            )
        self.trade = LogicalTrade(
            setup=spec["setup"],
            side=spec["side"],
            level_name=spec["level_name"],
            lyl=spec["level"],
            planned_entry=spec["entry"],
            initial_stop=spec["stop"],
            r_distance=abs(spec["entry"] - spec["stop"]),
            initial_risk=trade_risk,
            groups=list(group_ids),
            t1_group=group_ids[0],
            qty=qty,
            signal_index=spec["index"],
            placed_at=decision_time,
        )

    def _leg(self, spec: dict, qty: int, target: float | None, role: str) -> BracketLeg:
        self._tag += 1
        return BracketLeg(
            tag=f"{spec['setup']}-{spec['side'].value}-{role}-{self._tag}",
            side=spec["side"],
            qty=qty,
            entry_type=spec["entry_type"],
            entry_price=spec["entry"],
            stop_price=spec["stop"],
            target_price=target,
            setup=spec["setup"],
        )

    def _manage_open_trade(self, bar: Bar, index: int, decision_time: datetime) -> None:
        trade = self.trade
        if trade is None:
            return
        if trade.filled_qty == 0:
            if index - trade.signal_index >= self.config.exits.entry_order_timeout_bars:
                self._cancel_working_entries("entry timeout")
                self._close_logical(decision_time, "cancelled")
            return
        if self._hold_blocks(decision_time):
            return
        reason = self._exit_signal(bar, trade, decision_time)
        if reason:
            self._flatten(bar.close, decision_time, reason)
            return
        self._trail(trade)

    def _exit_signal(self, bar: Bar, trade: LogicalTrade, decision_time: datetime) -> str | None:
        if trade.entry_time is not None:
            held = (decision_time - trade.entry_time).total_seconds() / 60.0
            if not trade.t1_done and held >= self.config.exits.time_exit_minutes:
                return "time exit"
        self._ensure_indicators()
        ema_now = self._ema[-1] if self._ema else None
        atr_now = self._atr[-1] if self._atr else None
        if ema_now is not None:
            if trade.side is Side.LONG and bar.close < ema_now:
                return "close through 21 EMA"
            if trade.side is Side.SHORT and bar.close > ema_now:
                return "close through 21 EMA"
        band = self.config.exits.lyl_invalidation_r_fraction * trade.r_distance
        if trade.side is Side.LONG and bar.close < trade.lyl - band:
            return "close through the little yellow line"
        if trade.side is Side.SHORT and bar.close > trade.lyl + band:
            return "close through the little yellow line"
        if atr_now is not None and (bar.high - bar.low) > self.config.exits.volatility_exit_atr_multiple * atr_now:
            if trade.side is Side.LONG and bar.close < bar.open:
                return "volatility exit"
            if trade.side is Side.SHORT and bar.close > bar.open:
                return "volatility exit"
        if trade.t1_done and self._momentum_fade(trade.side):
            return "momentum fade"
        return None

    def _momentum_fade(self, side: Side) -> bool:
        need = self.config.exits.momentum_fade_bars
        lookback = self.config.filters.volume_lookback
        if len(self.bars_5) < need + 1:
            return False
        recent = self.bars_5[-need:]
        failing = True
        for prev, bar in zip(self.bars_5[-need - 1 : -1], recent):
            if side is Side.LONG and bar.high > prev.high:
                failing = False
            if side is Side.SHORT and bar.low < prev.low:
                failing = False
        if not failing:
            return False
        if len(self.bars_5) < lookback + 1:
            return False
        prior = [b.volume for b in self.bars_5[-(lookback + need) : -need]]
        if len(prior) < lookback:
            prior = [b.volume for b in self.bars_5[-(lookback + 1) : -1]]
        avg = sum(prior) / len(prior) if prior else 0
        if avg <= 0:
            return False
        return (sum(b.volume for b in recent) / need) < avg

    def _trail(self, trade: LogicalTrade) -> None:
        if not trade.t1_done:
            return
        self._ensure_indicators()
        swings = self._swing_low if trade.side is Side.LONG else self._swing_high
        tick = self.config.instrument.tick_size
        buffer = self.config.exits.trail_ticks_beyond_swing * tick
        new_stop = None
        if swings:
            swing = swings[-1][1]
            if trade.side is Side.LONG:
                new_stop = round_to_tick(swing - buffer, tick)
            else:
                new_stop = round_to_tick(swing + buffer, tick)
        if self.config.exits.move_stop_to_breakeven_after_first_target and trade.filled_qty > 0:
            be = trade.entry_notional / trade.filled_qty
            if new_stop is None:
                new_stop = be
            elif trade.side is Side.LONG:
                new_stop = max(new_stop, be)
            else:
                new_stop = min(new_stop, be)
        if new_stop is None:
            return
        for group_id in trade.groups:
            if self.broker.group_open(group_id):
                moved = self.broker.modify_stop(group_id, new_stop)
                if moved:
                    self.journal.order(group=group_id, action="modify_stop", stop=f"{new_stop:.2f}")

    def _hold_blocks(self, now: datetime) -> bool:
        trade = self.trade
        if trade is None or trade.entry_time is None:
            return False
        held = (now - trade.entry_time).total_seconds()
        return held < self.config.compliance.min_hold_seconds

    def _cancel_if_chasing(self, bar: Bar) -> None:
        trade = self.trade
        if trade is None or trade.filled_qty > 0:
            return
        limit = trade.lyl + self.config.exits.no_chase_r * trade.r_distance
        chase = bar.open > limit if trade.side is Side.LONG else bar.open < (
            trade.lyl - self.config.exits.no_chase_r * trade.r_distance
        )
        if chase:
            self._cancel_working_entries("no chase")
            self._close_logical(bar.time, "cancelled")

    def _cancel_working_entries(self, reason: str) -> None:
        if self.trade is None:
            return
        for group_id in self.trade.groups:
            cancel = getattr(self.broker, "cancel_entry", None)
            if cancel is not None:
                cancel(group_id)
            else:
                self.broker.cancel_all()
                return
        self.journal.order(action="cancel_entry", reason=reason)

    def _flatten(self, price: float, when: datetime, reason: str) -> None:
        # Warmup replays old 10:30 flattens through the same function. The live
        # broker refuses them while orders_enabled is false, so the log must
        # not call that a real order.
        sent = bool(getattr(self.broker, "orders_enabled", True))
        fills = self.broker.flatten(price, when, reason)
        if sent:
            self.journal.order(action="flatten", reason=reason, price=f"{price:.2f}")
        else:
            self.journal.info(
                f"REPLAY action=flatten reason={reason} price={price:.2f} not_sent=true"
            )
        self._consume(fills)
        if self.trade is not None:
            self._close_logical(when, reason)

    def _consume(self, fills: list[Fill]) -> None:
        for fill in fills:
            self.journal.fill(
                order=fill.order_id,
                tag=fill.tag,
                role=fill.role,
                side=fill.side.value,
                qty=fill.qty,
                price=f"{fill.price:.4f}",
                fee=f"{fill.fee:.2f}",
                time=fill.time.isoformat(),
            )
            trade = self.trade
            if trade is None or fill.group not in trade.groups:
                continue
            if fill.role == "entry":
                first = trade.entry_time is None
                trade.filled_qty += fill.qty
                trade.entry_notional += fill.price * fill.qty
                trade.entry_time = trade.entry_time or fill.time
                if first:
                    self.day.trades += 1
                    if self.day.trades >= self.config.risk.max_trades_per_day:
                        self.day.halted = True
                        self.day.halt_reason = "max trades for the day"
            if fill.group == trade.t1_group and fill.role == "target":
                trade.t1_done = True
        self._close_logical_if_flat(fills[-1].time if fills else None)

    def _close_logical_if_flat(self, when: datetime | None) -> None:
        trade = self.trade
        if trade is None or when is None:
            return
        if not trade.groups:
            return
        if any(not self.broker.group_closed(group_id) for group_id in trade.groups):
            return
        if trade.filled_qty == 0:
            self.trade = None
            return
        reasons = {self.broker.group_exit_reason(group_id) for group_id in trade.groups}
        if reasons == {"stop"}:
            reason = "stop"
        elif reasons == {"target"}:
            reason = "target"
        elif len(reasons) == 1:
            reason = next(iter(reasons))
        else:
            reason = "+".join(sorted(item for item in reasons if item))
        self._close_logical(when, reason)

    def _close_logical(self, when: datetime, reason: str) -> None:
        trade = self.trade
        if trade is None:
            return
        if any(not self.broker.group_closed(group_id) for group_id in trade.groups):
            if reason in {"cancelled", "entry timeout", "no chase"} and trade.filled_qty == 0:
                self.trade = None
            return
        pnl = sum(self.broker.group_realized(group_id) for group_id in trade.groups)
        if trade.filled_qty == 0:
            self.trade = None
            return
        entry = trade.entry_notional / trade.filled_qty
        r_mult = pnl / trade.initial_risk if trade.initial_risk else 0.0
        closed = ClosedTrade(
            setup=trade.setup,
            side=trade.side,
            qty=trade.filled_qty,
            entry_time=trade.entry_time or when,
            exit_time=when,
            entry_price=entry,
            exit_price=getattr(self.broker, "last_price", None) or entry,
            stop_price=trade.initial_stop,
            pnl=pnl,
            r_multiple=r_mult,
            exit_reason=reason,
            session_date=self.session_day,
        )
        self.closed.append(closed)
        self.journal.pnl(
            setup=trade.setup,
            side=trade.side.value,
            pnl=f"{pnl:.2f}",
            r=f"{r_mult:.2f}",
            reason=reason,
            session=self.session_day,
        )
        if reason == "stop" and pnl < 0:
            until = len(self.bars_5) + self.config.exits.cooldown_bars_after_stop
            self.cooldown_until[trade.side] = max(self.cooldown_until[trade.side], until)
        if pnl < 0:
            self.day.consecutive_losses += 1
        elif pnl > 0:
            self.day.consecutive_losses = 0
        if self.day.consecutive_losses >= self.config.risk.max_consecutive_losses:
            self.day.halted = True
            self.day.halt_reason = "consecutive-loss stop"
        self.trade = None

    def _enforce_daily_limits(self, price: float, when: datetime) -> None:
        mtm = self.day.realized + self.day.unrealized
        reason = None
        if mtm <= -self.config.risk.daily_max_loss:
            reason = "daily max loss"
        elif mtm >= self.config.risk.daily_profit_stop:
            reason = "daily profit stop"
        if reason is None:
            return
        self.day.halted = True
        self.day.halt_reason = reason
        if self.broker.net_qty() != 0 or self.broker.working_entry_count():
            self._flatten(price, when, reason)

    def _sync_pnl(self, price: float) -> None:
        self.day.realized = sum(self.broker.group_realized(group_id) for group_id in self.session_groups)
        self.day.unrealized = self.broker.unrealized(price)
        self.day.open_risk = self.broker.open_risk()

    def _mark(self, price: float, when: datetime) -> None:
        self._sync_pnl(price)
        if self.session_day is None:
            return
        mtm = self.day.realized + self.day.unrealized
        current = self.daily_mtm_low.get(self.session_day)
        if current is None or mtm < current:
            self.daily_mtm_low[self.session_day] = mtm
        equity = self._equity()
        self.equity_curve.append((when, equity))

    def _equity(self) -> float:
        return self.config.account.starting_balance + self.cumulative_prior + self.day.realized + self.day.unrealized

    def _indicators_ready(self) -> bool:
        need = max(
            self.config.filters.ema_period + self.config.filters.chop_slope_bars,
            self.config.filters.atr_period,
            self.config.filters.volume_lookback + 1,
        )
        return len(self.bars_5) >= need and self.vwap is not None

    def _chop(self) -> bool:
        self._ensure_indicators()
        need = max(self.config.filters.chop_lookback_bars, self.config.filters.chop_slope_bars) + 1
        closes = [bar.close for bar in self.bars_5[-need:]]
        emas = self._ema[-need:]
        atr_now = self._atr[-1] if self._atr else None
        return is_chop(
            closes,
            emas,
            atr_now,
            cross_limit=self.config.filters.chop_ema_crosses,
            cross_lookback=self.config.filters.chop_lookback_bars,
            slope_bars=self.config.filters.chop_slope_bars,
            slope_atr_fraction=self.config.filters.chop_slope_atr_fraction,
        )

    def _ensure_indicators(self) -> None:
        n = len(self.bars_5)
        if self._ind_n == n:
            return
        if self._ind_n == 0 or n != self._ind_n + 1:
            self._rebuild_indicators()
            return
        self._append_indicator()

    def _rebuild_indicators(self) -> None:
        closes = [bar.close for bar in self.bars_5]
        filters = self.config.filters
        self._ema = ema(closes, filters.ema_period)
        self._atr = atr(self.bars_5, filters.atr_period)
        self._trs = true_ranges(self.bars_5) if self.bars_5 else []
        self._swing_high = swing_points(self.bars_5, filters.swing_bars_each_side, "high")
        self._swing_low = swing_points(self.bars_5, filters.swing_bars_each_side, "low")
        self._ind_n = len(self.bars_5)

    def _append_indicator(self) -> None:
        filters = self.config.filters
        bar = self.bars_5[-1]
        n = len(self.bars_5)
        period = filters.ema_period
        if n < period:
            self._ema.append(None)
        elif n == period:
            self._ema.append(sum(item.close for item in self.bars_5[:period]) / period)
        else:
            k = 2.0 / (period + 1)
            prev = self._ema[-1]
            self._ema.append(bar.close * k + prev * (1.0 - k))
        if n == 1:
            true_range = bar.high - bar.low
        else:
            prev_close = self.bars_5[-2].close
            true_range = max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
        self._trs.append(true_range)
        atr_period = filters.atr_period
        if n < atr_period:
            self._atr.append(None)
        elif n == atr_period:
            self._atr.append(sum(self._trs[:atr_period]) / atr_period)
        else:
            prev_atr = self._atr[-1]
            self._atr.append((prev_atr * (atr_period - 1) + true_range) / atr_period)
        self._note_new_swings()
        self._ind_n = n

    def _note_new_swings(self) -> None:
        wing = self.config.filters.swing_bars_each_side
        index = len(self.bars_5) - 1 - wing
        if index < wing:
            return
        if self._is_swing(index, "high"):
            self._swing_high.append((index, self.bars_5[index].high))
        if self._is_swing(index, "low"):
            self._swing_low.append((index, self.bars_5[index].low))

    def _is_swing(self, index: int, kind: str) -> bool:
        wing = self.config.filters.swing_bars_each_side
        bars = self.bars_5
        if kind == "high":
            price = bars[index].high
            return all(price > bars[index - k].high for k in range(1, wing + 1)) and all(
                price > bars[index + k].high for k in range(1, wing + 1)
            )
        price = bars[index].low
        return all(price < bars[index - k].low for k in range(1, wing + 1)) and all(
            price < bars[index + k].low for k in range(1, wing + 1)
        )

    def _trend_ok(self, side: Side) -> bool:
        self._ensure_indicators()
        highs = self._swing_high
        lows = self._swing_low
        if len(highs) < 2 or len(lows) < 2:
            return False
        if side is Side.LONG:
            return highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
        return highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]

    def _break_is_valid(self, bar: Bar, level: float, side: Side) -> bool:
        return self._break_block_reason(bar, level, side) is None

    def _ignoring(self, flag: str) -> bool:
        study = self.study
        return study is not None and bool(getattr(study, flag))

    def _entries_open(self, decision_time: datetime) -> bool:
        study = self.study
        if study is None or study.entry_end is None:
            return entries_allowed(decision_time, self.config.session)
        session = replace(self.config.session, entry_end=study.entry_end)
        return entries_allowed(decision_time, session)

    def _retest_tolerance(self) -> float:
        ticks = self.config.exits.retest_tolerance_ticks
        study = self.study
        if study is not None and study.retest_tolerance_ticks is not None:
            ticks = study.retest_tolerance_ticks
        return ticks * self.config.instrument.tick_size

    def _audit_name(self, name: str, bar: Bar, detail: str) -> None:
        study = self.study
        if study is None or not study.audit:
            return
        key = (name, bar.time.isoformat(), detail)
        if key in study._seen:
            return
        study._seen.add(key)
        study.blocks.append({"filter": name, "bar": bar.time.isoformat(), "detail": detail})

    def _audit_cutoff(self, bar: Bar, decision_time: datetime) -> None:
        if self.study is None or not self.study.audit:
            return
        if entries_allowed(decision_time, self.config.session) or must_flatten(decision_time, self.config.session):
            return
        opening = self.opening
        if opening is None:
            return
        if bar.close > opening.high or bar.close < opening.low:
            self._audit_name("cutoff", bar, "or")

    def _audit_retest_depth(self, bar: Bar, item: PendingBreak) -> None:
        if self.study is None or not self.study.audit:
            return
        if self._closed_back_through(bar, item):
            return
        tick = self.config.instrument.tick_size
        if tick <= 0:
            return
        if item.side is Side.LONG:
            if bar.close <= item.level:
                return
            depth = (item.level - bar.low) / tick
        else:
            if bar.close >= item.level:
                return
            depth = (bar.high - item.level) / tick
        allowed = self.config.exits.retest_tolerance_ticks
        wider = self.study.audit_retest_ticks
        if wider is not None:
            live_band = allowed * tick
            wide_band = wider * tick
            if is_retest_bar(bar, item.level, item.side, wide_band) and not is_retest_bar(
                bar, item.level, item.side, live_band
            ):
                self._audit_name("retest", bar, f"{item.level_name}:{depth:.1f}")
            return
        if depth > allowed:
            self._audit_name("retest", bar, f"{item.level_name}:{depth:.1f}")

    def _audit_opening_break(self, bar: Bar) -> None:
        """Record which rules would reject a close through the opening range.

        This does not place or block an order. It runs only when a report asks.
        """
        if self.opening is None or not self._indicators_ready():
            return
        if self._chop() and (bar.close > self.opening.high or bar.close < self.opening.low):
            self._audit_name("chop", bar, "or")
        for side, level_name, level in (
            (Side.LONG, "or_high", self.opening.high),
            (Side.SHORT, "or_low", self.opening.low),
        ):
            beyond = bar.close > level if side is Side.LONG else bar.close < level
            if not beyond:
                continue
            saved = self.study
            self.study = None
            try:
                reason = self._break_block_reason(bar, level, side)
            finally:
                self.study = saved
            mapped = {
                "not above the 5-minute average and VWAP": "vwap_ema",
                "not below the 5-minute average and VWAP": "vwap_ema",
                "too far from the 5-minute average": "atr",
                "volume is not strong enough": "volume",
            }.get(reason or "")
            if mapped:
                self._audit_name(mapped, bar, level_name)

    def _narrate_quiet_bar(self, bar: Bar) -> None:
        """Say why a live 5-minute bar did not become an order. Does not trade."""
        if not self._should_narrate():
            return
        stamp = bar.time.isoformat()
        minutes = self.config.filters.signal_timeframe_minutes
        opening = self.opening
        if opening is not None and bar.close > opening.high:
            reason = self._break_block_reason(bar, opening.high, Side.LONG)
            if reason:
                self.last_watch = reason
                self.journal.info(
                    f"WATCH bar={stamp} timeframe={minutes} level=or_high side=long result={reason}"
                )
                return
        if opening is not None and bar.close < opening.low:
            reason = self._break_block_reason(bar, opening.low, Side.SHORT)
            if reason:
                self.last_watch = reason
                self.journal.info(
                    f"WATCH bar={stamp} timeframe={minutes} level=or_low side=short result={reason}"
                )
                return
        self.last_watch = "no setup"
        self.journal.info(f"WATCH bar={stamp} timeframe={minutes} result=no setup")

    def _should_narrate(self) -> bool:
        # Practice only, and only after warmup has turned orders back on.
        # Backtests stay quiet. A replayed historical day is not "right now".
        return (
            self.armed
            and getattr(self.broker, "name", "") == "projectx"
            and bool(getattr(self.broker, "orders_enabled", False))
        )

    def _break_block_reason(self, bar: Bar, level: float, side: Side) -> str | None:
        """Why this bar is not a valid break. None means the break is valid."""
        filters = self.config.filters
        self._ensure_indicators()
        emas = self._ema
        atrs = self._atr
        ema_now = emas[-1] if emas else None
        atr_now = atrs[-1] if atrs else None
        if ema_now is None or atr_now is None or self.vwap is None:
            return "indicators are not ready"
        if not self._ignoring("ignore_vwap_ema"):
            if side is Side.LONG and not (bar.close > self.vwap and bar.close > ema_now):
                return "not above the 5-minute average and VWAP"
            if side is Side.SHORT and not (bar.close < self.vwap and bar.close < ema_now):
                return "not below the 5-minute average and VWAP"
        if not self._ignoring("ignore_atr"):
            if abs(bar.close - ema_now) > filters.overextended_atr * atr_now:
                return "too far from the 5-minute average"
        volumes = [b.volume for b in self.bars_5[-(filters.volume_lookback + 1) :]]
        if not self._ignoring("ignore_volume"):
            if not volume_confirmed(volumes, filters.volume_lookback, filters.volume_multiple):
                return "volume is not strong enough"
        if not is_strong_break(
            bar,
            level,
            side,
            self.config.instrument.tick_size,
            filters.strong_candle_body_fraction,
            filters.strong_candle_close_location,
        ):
            return "5-minute candle is not a strong close through the level"
        if filters.use_macd_filter:
            closes = [b.close for b in self.bars_5]
            _line, _sig, hist = macd(closes, filters.macd_fast, filters.macd_slow, filters.macd_signal)
            if hist[-1] is None:
                return "indicators are not ready"
            if side is Side.LONG and hist[-1] <= 0:
                return "momentum is not with the long"
            if side is Side.SHORT and hist[-1] >= 0:
                return "momentum is not with the short"
        if self._side_blocked(side, len(self.bars_5) - 1):
            return "that direction is on cooldown"
        return None

    def _closed_back_through(self, bar: Bar, item: PendingBreak) -> bool:
        if item.side is Side.LONG:
            return bar.close < item.level
        return bar.close > item.level

    def _slot_used(self, setup: str, level_name: str, side: Side) -> bool:
        return any(
            item.setup == setup and item.level_name == level_name and item.side is side
            for item in self.pending
        )

    def _side_blocked(self, side: Side, index: int) -> bool:
        return index <= self.cooldown_until[side]

    def _bar_opens_in_entry_window(self, bar: Bar) -> bool:
        if self.session_day is None:
            return False
        start = at_clock(self.session_day, self.config.session.entry_start, self.config.session.timezone)
        return bar.time >= start

    def _level_price(self, name: str) -> float | None:
        if name == "vwap":
            return self.vwap
        for level in self.levels:
            if level.name == name:
                return level.price
        return None

    def _entries_still_working(self) -> bool:
        if self.trade is None:
            return False
        return any(not self.broker.group_closed(group_id) for group_id in self.trade.groups)

    def _skip(self, reason: str, bar: Bar) -> None:
        self.skips[reason] = self.skips.get(reason, 0) + 1
        self.journal.skip(reason=reason, bar=bar.time.isoformat())

    def _bar_opens_after_range(self, bar: Bar) -> bool:
        if self.session_day is None:
            return False
        start = at_clock(self.session_day, self.config.session.opening_range_end, self.config.session.timezone)
        return bar.time >= start

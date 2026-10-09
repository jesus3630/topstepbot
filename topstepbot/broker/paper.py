"""Offline paper broker. Fills are simulated from 1-minute bars.

Same-bar rule when a stop and a target are both inside the bar: the stop fills
first. That is the conservative path for a prop-firm loss check. Slippage is
applied against the trader on stops and on entries. Limit targets fill at the
limit, or at a gapped open if the open is through the limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from topstepbot.broker.base import require_stop
from topstepbot.config import BotConfig
from topstepbot.models import Bar, BracketLeg, Fill, Side


@dataclass
class _Group:
    id: str
    leg: BracketLeg
    stop_price: float
    target_price: float | None
    entry_filled: bool = False
    qty_open: int = 0
    entry_price: float = 0.0
    realized: float = 0.0
    fees: float = 0.0
    closed: bool = False
    exit_reason: str = ""
    entry_working: bool = True
    target_working: bool = True


class PaperBroker:
    name = "paper"

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._groups: dict[str, _Group] = {}
        self._seq = 0
        self.last_price: float | None = None
        self.slippage = config.risk.slippage_ticks * config.instrument.tick_size
        self.point_value = config.dollars_per_point
        self.fee_per_side = config.risk.round_turn_fee_per_contract / 2.0
        self._fills: list[Fill] = []

    def place_brackets(self, legs: list[BracketLeg]) -> list[str]:
        ids: list[str] = []
        for leg in legs:
            require_stop(leg)
            self._seq += 1
            group_id = f"paper-{self._seq}"
            target_working = leg.target_price is not None
            self._groups[group_id] = _Group(
                id=group_id,
                leg=leg,
                stop_price=leg.stop_price,
                target_price=leg.target_price,
                target_working=target_working,
            )
            ids.append(group_id)
        return ids

    def on_bar(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        for group in list(self._groups.values()):
            if group.closed:
                continue
            if not group.entry_filled and group.entry_working:
                entry = self._try_entry(group, bar)
                if entry is not None:
                    fills.append(entry)
                    fills.extend(self._manage_open(group, bar))
            elif group.entry_filled and group.qty_open > 0:
                fills.extend(self._manage_open(group, bar))
        self.last_price = bar.close
        self._fills.extend(fills)
        return fills

    def cancel_all(self) -> None:
        for group in self._groups.values():
            group.entry_working = False
            group.target_working = False
            if group.qty_open <= 0 and not group.closed and not group.entry_filled:
                group.closed = True
                group.exit_reason = group.exit_reason or "cancelled"

    def cancel_entry(self, group_id: str) -> bool:
        group = self._groups.get(group_id)
        if group is None or group.entry_filled or group.closed:
            return False
        group.entry_working = False
        group.target_working = False
        group.closed = True
        group.exit_reason = "cancelled"
        return True

    def flatten(self, price: float, when: datetime, reason: str = "flatten") -> list[Fill]:
        fills: list[Fill] = []
        for group in self._groups.values():
            if group.qty_open > 0 and not group.closed:
                raw = self._worse(group.leg.side.opposite, price)
                fills.append(self._close(group, reason, raw, when))
            elif not group.entry_filled and not group.closed:
                group.entry_working = False
                group.closed = True
                group.exit_reason = "cancelled"
            group.target_working = False
        self.last_price = price
        self._fills.extend(fills)
        return fills

    def modify_stop(self, group_id: str, new_stop: float) -> bool:
        group = self._groups.get(group_id)
        if group is None or group.closed or group.qty_open <= 0:
            return False
        if group.leg.side is Side.LONG and new_stop <= group.stop_price + 1e-9:
            return False
        if group.leg.side is Side.SHORT and new_stop >= group.stop_price - 1e-9:
            return False
        if self.last_price is not None:
            if group.leg.side is Side.LONG and new_stop >= self.last_price - 1e-9:
                return False
            if group.leg.side is Side.SHORT and new_stop <= self.last_price + 1e-9:
                return False
        group.stop_price = new_stop
        return True

    def net_qty(self) -> int:
        total = 0
        for group in self._groups.values():
            if group.qty_open <= 0:
                continue
            sign = 1 if group.leg.side is Side.LONG else -1
            total += sign * group.qty_open
        return total

    def average_price(self) -> float | None:
        qty = 0
        notion = 0.0
        for group in self._groups.values():
            if group.qty_open <= 0:
                continue
            qty += group.qty_open
            notion += group.entry_price * group.qty_open
        if qty == 0:
            return None
        return notion / qty

    def unrealized(self, price: float) -> float:
        total = 0.0
        for group in self._groups.values():
            if group.qty_open <= 0:
                continue
            total += self._points(group.leg.side, group.entry_price, price) * self.point_value * group.qty_open
        return total

    def open_risk(self) -> float:
        total = 0.0
        for group in self._groups.values():
            if group.qty_open <= 0:
                continue
            distance = abs(group.entry_price - group.stop_price)
            slip = self.config.risk.slippage_ticks * self.config.instrument.tick_value * group.qty_open
            fee = self.fee_per_side * group.qty_open
            total += distance * self.point_value * group.qty_open + slip + fee
        return total

    def poll(self, when: datetime) -> list[Fill]:
        return []

    def working_entry_count(self) -> int:
        return sum(1 for group in self._groups.values() if group.entry_working and not group.entry_filled and not group.closed)

    def group_open(self, group_id: str) -> bool:
        group = self._groups.get(group_id)
        return bool(group and group.qty_open > 0 and not group.closed)

    def group_realized(self, group_id: str) -> float:
        group = self._groups[group_id]
        return group.realized

    def group_closed(self, group_id: str) -> bool:
        return self._groups[group_id].closed

    def group_exit_reason(self, group_id: str) -> str:
        return self._groups[group_id].exit_reason

    def stop_price(self, group_id: str) -> float | None:
        group = self._groups.get(group_id)
        if group is None or group.closed:
            return None
        return group.stop_price

    def has_protective_stop(self, group_id: str) -> bool:
        group = self._groups.get(group_id)
        if group is None:
            return False
        return group.stop_price is not None and (group.entry_working or group.qty_open > 0)

    def _try_entry(self, group: _Group, bar: Bar) -> Fill | None:
        leg = group.leg
        if leg.entry_type == "market":
            raw = self._worse(leg.side, bar.open)
            return self._open(group, raw, bar.time)
        assert leg.entry_price is not None
        trigger = leg.entry_price
        if leg.side is Side.LONG:
            if bar.high < trigger - 1e-9:
                return None
            raw = trigger if bar.open <= trigger + 1e-9 else bar.open
            raw = self._worse(Side.LONG, raw)
            return self._open(group, raw, bar.time)
        if bar.low > trigger + 1e-9:
            return None
        raw = trigger if bar.open >= trigger - 1e-9 else bar.open
        raw = self._worse(Side.SHORT, raw)
        return self._open(group, raw, bar.time)

    def _manage_open(self, group: _Group, bar: Bar) -> list[Fill]:
        if group.qty_open <= 0 or group.closed:
            return []
        stop = group.stop_price
        target = group.target_price if group.target_working else None
        if group.leg.side is Side.LONG:
            hit_stop = bar.low <= stop + 1e-9
            hit_target = target is not None and bar.high >= target - 1e-9
            if hit_stop:
                raw = stop if bar.open >= stop - 1e-9 else bar.open
                raw = self._worse(Side.SHORT, raw)
                return [self._close(group, "stop", raw, bar.time)]
            if hit_target:
                assert target is not None
                raw = target if bar.open <= target + 1e-9 else bar.open
                return [self._close(group, "target", raw, bar.time)]
            return []
        hit_stop = bar.high >= stop - 1e-9
        hit_target = target is not None and bar.low <= target + 1e-9
        if hit_stop:
            raw = stop if bar.open <= stop + 1e-9 else bar.open
            raw = self._worse(Side.LONG, raw)
            return [self._close(group, "stop", raw, bar.time)]
        if hit_target:
            assert target is not None
            raw = target if bar.open >= target - 1e-9 else bar.open
            return [self._close(group, "target", raw, bar.time)]
        return []

    def _open(self, group: _Group, price: float, when: datetime) -> Fill:
        qty = group.leg.qty
        fee = self.fee_per_side * qty
        group.entry_filled = True
        group.entry_working = False
        group.qty_open = qty
        group.entry_price = price
        group.fees += fee
        group.realized -= fee
        return Fill(
            order_id=f"{group.id}-entry",
            tag=group.leg.tag,
            group=group.id,
            role="entry",
            side=group.leg.side,
            qty=qty,
            price=price,
            time=when,
            fee=fee,
        )

    def _close(self, group: _Group, role: str, price: float, when: datetime) -> Fill:
        qty = group.qty_open
        fee = self.fee_per_side * qty
        gross = self._points(group.leg.side, group.entry_price, price) * self.point_value * qty
        group.realized += gross - fee
        group.fees += fee
        group.qty_open = 0
        group.closed = True
        group.entry_working = False
        group.target_working = False
        group.exit_reason = role
        exit_side = group.leg.side.opposite
        return Fill(
            order_id=f"{group.id}-{role}",
            tag=group.leg.tag,
            group=group.id,
            role=role,
            side=exit_side,
            qty=qty,
            price=price,
            time=when,
            fee=fee,
        )

    def _worse(self, order_side: Side, price: float) -> float:
        if self.slippage <= 0:
            return price
        if order_side is Side.LONG:
            return price + self.slippage
        return price - self.slippage

    @staticmethod
    def _points(position_side: Side, entry: float, exit_price: float) -> float:
        if position_side is Side.LONG:
            return exit_price - entry
        return entry - exit_price

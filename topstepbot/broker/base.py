"""Broker interface shared by the paper simulator and the ProjectX client.

Every entry must carry a protective stop. Implementations reject a leg that
does not include one.
"""

from __future__ import annotations

from typing import Protocol

from topstepbot.models import AccountInfo, Bar, BracketLeg, Fill, PositionSnapshot


class Broker(Protocol):
    name: str

    def place_brackets(self, legs: list[BracketLeg]) -> list[str]:
        """Submit entry orders. Each leg's stop is sent with the entry.

        Returns the group id of each accepted leg, in order.
        """

    def cancel_all(self) -> None:
        """Cancel every working order. Does not by itself close a position."""

    def flatten(self, price: float, when) -> list[Fill]:
        """Cancel working orders and close the open position at `price`."""

    def modify_stop(self, group_id: str, new_stop: float) -> bool:
        """Move a protective stop tighter. Must never widen it. Returns False if ignored."""

    def on_bar(self, bar: Bar) -> list[Fill]:
        """Paper brokers fill against this bar. Live brokers return an empty list."""

    def net_qty(self) -> int:
        """Signed contracts. Positive is long."""

    def average_price(self) -> float | None:
        ...

    def unrealized(self, price: float) -> float:
        ...

    def open_risk(self) -> float:
        """Positive dollars that could still be lost if protective stops fill."""

    def working_entry_count(self) -> int:
        ...

    def group_open(self, group_id: str) -> bool:
        ...

    def group_realized(self, group_id: str) -> float:
        ...

    def group_closed(self, group_id: str) -> bool:
        ...

    def group_exit_reason(self, group_id: str) -> str:
        ...

    def stop_price(self, group_id: str) -> float | None:
        ...


class ProtectiveStopRequired(RuntimeError):
    """Raised when an entry would be sent without a broker-side stop."""


def require_stop(leg: BracketLeg) -> None:
    if leg.qty < 1:
        raise ProtectiveStopRequired("refusing an entry with no contracts")
    if leg.stop_price is None:
        raise ProtectiveStopRequired("every entry must carry a protective stop")
    if leg.entry_type == "stop":
        if leg.entry_price is None:
            raise ProtectiveStopRequired("a stop entry needs an entry price")
        if leg.side.value == "long" and leg.stop_price >= leg.entry_price:
            raise ProtectiveStopRequired("long stop must be below the entry")
        if leg.side.value == "short" and leg.stop_price <= leg.entry_price:
            raise ProtectiveStopRequired("short stop must be above the entry")
    if leg.entry_type == "market" and leg.entry_price is not None:
        if leg.side.value == "long" and leg.stop_price >= leg.entry_price:
            raise ProtectiveStopRequired("long stop must be below the planned entry")
        if leg.side.value == "short" and leg.stop_price <= leg.entry_price:
            raise ProtectiveStopRequired("short stop must be above the planned entry")


class AccountRejected(RuntimeError):
    """The account looks like a Topstep Live Funded account, or cannot be classified."""


def position_side_from_qty(qty: int):
    from topstepbot.models import Side

    if qty > 0:
        return Side.LONG
    if qty < 0:
        return Side.SHORT
    return None


def snapshot_from_net(contract_id: str, qty: int, average: float | None) -> PositionSnapshot | None:
    from topstepbot.models import Side

    if qty == 0 or average is None:
        return None
    side = Side.LONG if qty > 0 else Side.SHORT
    return PositionSnapshot(contract_id=contract_id, side=side, qty=abs(qty), average_price=average)

"""Shared data types. Prices are floats rounded to the instrument tick."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def opposite(self) -> "Side":
        return Side.SHORT if self is Side.LONG else Side.LONG


class Phase(str, Enum):
    BEFORE = "before"
    OPENING_RANGE = "opening_range"
    ENTRY = "entry"
    MANAGE = "manage"
    FLAT = "flat"


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar. `time` is the bar's open time, timezone-aware."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.time.tzinfo is None:
            raise ValueError("bar timestamps must be timezone-aware")
        if self.high < self.low:
            raise ValueError("bar high is below low")


@dataclass(frozen=True)
class PriceLevel:
    name: str
    price: float


@dataclass(frozen=True)
class OpeningRange:
    session_date: object
    high: float
    low: float


@dataclass
class Signal:
    setup: str
    side: Side
    level_name: str
    level_price: float
    entry_price: float
    stop_price: float
    target_price: float
    runner_target: float | None
    bar_time: datetime
    reason: str


@dataclass
class BracketLeg:
    """One entry order that must carry its own protective stop."""

    tag: str
    side: Side
    qty: int
    entry_type: str  # "stop" or "market"
    entry_price: float | None
    stop_price: float
    target_price: float | None
    setup: str


@dataclass
class BrokerOrder:
    id: str
    tag: str
    group: str
    role: str  # entry, stop, target
    side: Side  # order side: buy to open a long, sell to close a long
    qty: int
    order_type: str  # market, stop, limit
    price: float | None
    status: str = "working"  # working, filled, cancelled
    filled_qty: int = 0
    avg_fill: float | None = None


@dataclass
class Fill:
    order_id: str
    tag: str
    group: str
    role: str
    side: Side
    qty: int
    price: float
    time: datetime
    fee: float


@dataclass
class PositionSnapshot:
    contract_id: str
    side: Side
    qty: int
    average_price: float


@dataclass
class ClosedTrade:
    setup: str
    side: Side
    qty: int
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    pnl: float
    r_multiple: float
    exit_reason: str
    session_date: object
    fees: float = 0.0


@dataclass
class DayStats:
    realized: float = 0.0
    unrealized: float = 0.0
    open_risk: float = 0.0
    trades: int = 0
    consecutive_losses: int = 0
    halted: bool = False
    halt_reason: str = ""
    fees: float = 0.0


@dataclass
class AccountInfo:
    id: int
    name: str
    can_trade: bool | None = None
    is_visible: bool | None = None
    balance: float | None = None
    # None means the payload did not say. SignalR accounts include this flag;
    # the REST search example does not. TODO-VERIFY on a live response.
    simulated: bool | None = None
    raw: dict = field(default_factory=dict)

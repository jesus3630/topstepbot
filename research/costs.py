"""Costs and size for the research backtest.

TopstepX published MES round-turn total is $1.22: $0.50 commission, $0.70
exchange, $0.02 NFA regulatory. The same page lists Micro E-mini Nasdaq-100
(MNQ) at a $1.22 round-turn total, next to MES. It does not print a separate
commission, exchange, and NFA split for MNQ, so this module does not invent
one. Source, read 2026-10-10:
https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees

The October 2026 CME fee increase on that page is MCL and MNG, not MES or MNQ.
The live bot's $1.40 placeholder in config/settings.yaml is intentionally
left alone.

MES is $1.25 a tick ($5 a point). MNQ is $0.50 a tick ($2 a point). Both
ticks are 0.25 points. ``python -m research.run --instrument MNQ`` prices
the same rules with the MNQ spec. The default remains MES.
"""

from __future__ import annotations

import math
from contextvars import ContextVar
from dataclasses import dataclass

TICK = 0.25
POINT_VALUE = 5.0
FEE_ROUND_TURN = 1.22
FEE_SOURCE = "https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees"
SLIP_TICKS = 1
RISK_DOLLARS = 200.0
MAX_CONTRACTS = 50
DAILY_STOP = 400.0
MAX_TRADES_PER_DAY = 2


@dataclass(frozen=True)
class Instrument:
    """Dollars and the Databento continuous symbol for one micro contract."""

    code: str
    tick: float
    point_value: float
    fee_round_turn: float
    continuous: str
    fee_note: str

    @property
    def tick_value(self) -> float:
        return self.tick * self.point_value


MES = Instrument(
    code="MES",
    tick=TICK,
    point_value=POINT_VALUE,
    fee_round_turn=FEE_ROUND_TURN,
    continuous="MES.v.0",
    fee_note="commission $0.50 + exchange $0.70 + NFA $0.02",
)
MNQ = Instrument(
    code="MNQ",
    tick=0.25,
    point_value=2.0,
    fee_round_turn=FEE_ROUND_TURN,
    continuous="MNQ.v.0",
    fee_note="published round-turn total on the same page, listed next to MES",
)
INSTRUMENTS = {MES.code: MES, MNQ.code: MNQ}
_ACTIVE: ContextVar[Instrument | None] = ContextVar("research_instrument", default=None)


def resolve_instrument(instrument: Instrument | str | None = None) -> Instrument:
    """MES when omitted. A code is case-insensitive."""
    if instrument is None or instrument == "":
        return MES
    if isinstance(instrument, Instrument):
        return instrument
    key = str(instrument).strip().upper()
    found = INSTRUMENTS.get(key)
    if found is None:
        known = ", ".join(INSTRUMENTS)
        raise SystemExit(f"Unknown instrument {instrument!r}. Choose {known}.")
    return found


def active_instrument() -> Instrument:
    current = _ACTIVE.get()
    return MES if current is None else current


class using_instrument:
    """Size and score trades as this contract until the block ends."""

    def __init__(self, instrument: Instrument | str) -> None:
        self.instrument = instrument if isinstance(instrument, Instrument) else resolve_instrument(instrument)
        self._token = None

    def __enter__(self) -> Instrument:
        self._token = _ACTIVE.set(self.instrument)
        return self.instrument

    def __exit__(self, *exc) -> None:
        _ACTIVE.reset(self._token)


def round_tick(price: float, tick: float = TICK) -> float:
    return round(price / tick) * tick


def contracts_for_stop(
    stop_points: float,
    *,
    risk: float = RISK_DOLLARS,
    fee: float = FEE_ROUND_TURN,
    tick: float = TICK,
    point_value: float = POINT_VALUE,
    slip_ticks_round_turn: int = 2,
    max_contracts: int = MAX_CONTRACTS,
) -> int:
    """How many contracts to trade so a full stop, both slips, and the fee fit in `risk`.

    Returns 0 when one contract already risks more than `risk`. Caller skips.
    """
    if stop_points <= 0 or risk <= 0 or point_value <= 0 or max_contracts < 1:
        return 0
    # Market and stop orders pay 1 tick each. A limit entry pays none, so a
    # limit-in / stop-out trade passes slip_ticks_round_turn=1.
    slip = slip_ticks_round_turn * tick * point_value
    cost = stop_points * point_value + slip + fee
    if cost <= 0:
        return 0
    sized = math.floor(risk / cost)
    if sized < 1:
        return 0
    return int(min(sized, max_contracts))

"""Costs and size for the research backtest.

TopstepX published MES round-turn total is $1.22: $0.50 commission, $0.70
exchange, $0.02 NFA regulatory. Source, read 2026-10-10:
https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees

The October 2026 CME fee increase on that page is MCL and MNG, not MES.
The live bot's $1.40 placeholder in config/settings.yaml is intentionally
left alone.
"""

from __future__ import annotations

import math

TICK = 0.25
POINT_VALUE = 5.0
FEE_ROUND_TURN = 1.22
FEE_SOURCE = "https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees"
SLIP_TICKS = 1
RISK_DOLLARS = 200.0
MAX_CONTRACTS = 50
DAILY_STOP = 400.0
MAX_TRADES_PER_DAY = 2


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
    """How many MES to trade so a full stop, both slips, and the fee fit in `risk`.

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

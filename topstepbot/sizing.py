"""Contract sizing from the stop distance. Never tighten a stop to fit size."""

from __future__ import annotations

import math


def contracts_for_risk(
    *,
    entry: float,
    stop: float,
    tick_size: float,
    tick_value: float,
    risk_per_trade: float,
    slippage_ticks: int,
    round_turn_fee: float,
    max_contracts: int,
    topstep_max_contracts: int,
) -> int:
    """floor(risk / (stop ticks x tick value + slippage + fees)), then cap.

    Returns 0 when the stop is too wide for one contract or inputs are unusable.
    The caller skips the trade in that case.
    """
    if tick_size <= 0 or tick_value <= 0 or risk_per_trade <= 0:
        return 0
    if max_contracts < 1 or topstep_max_contracts < 1:
        return 0
    stop_ticks = abs(entry - stop) / tick_size
    if stop_ticks <= 0:
        return 0
    cost = stop_ticks * tick_value + slippage_ticks * tick_value + round_turn_fee
    if cost <= 0:
        return 0
    sized = math.floor(risk_per_trade / cost)
    sized = min(sized, max_contracts, topstep_max_contracts)
    if sized < 1:
        return 0
    return int(sized)


def dollar_risk(contracts: int, entry: float, stop: float, tick_size: float, tick_value: float, slippage_ticks: int, round_turn_fee: float) -> float:
    if contracts <= 0 or tick_size <= 0:
        return 0.0
    stop_ticks = abs(entry - stop) / tick_size
    per = stop_ticks * tick_value + slippage_ticks * tick_value + round_turn_fee
    return contracts * per


def split_quantity(qty: int, partial_fraction: float) -> tuple[int, int]:
    """Return (first-target qty, runner qty). One contract exits fully at target 1."""
    if qty <= 1:
        return qty, 0
    first = math.floor(qty * partial_fraction)
    if first < 1:
        first = 1
    if first >= qty:
        return qty, 0
    return first, qty - first

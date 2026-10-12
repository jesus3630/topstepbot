"""One-trade fill simulator.

Market and stop orders pay 1 tick of adverse slippage. A stop fills when
price touches it. A limit fills only when price trades through it, with no
slippage. When a stop and a target are both inside the same bar, the stop
fills. A still-open trade flattens at the session deadline.
"""

from __future__ import annotations

from dataclasses import dataclass

from research.costs import FEE_ROUND_TURN, POINT_VALUE, TICK


@dataclass(frozen=True)
class TradeResult:
    filled: bool
    entry: float = 0.0
    exit: float = 0.0
    reason: str = ""
    pnl: float = 0.0
    mae: float = 0.0
    fill_index: int = -1
    exit_index: int = -1


def simulate(
    opens,
    highs,
    lows,
    closes,
    *,
    side: str,
    entry_index: int,
    kind: str,
    level: float | None,
    stop: float | None,
    target: float | None,
    last_index: int,
    exit_on: str,
    qty: int,
    fee_rt: float = FEE_ROUND_TURN,
    tick: float = TICK,
    point_value: float = POINT_VALUE,
) -> TradeResult:
    """Simulate one order. `exit_on='open'` flattens at `last_index` open and
    does not use that bar's high or low. `exit_on='close'` may use that bar,
    then flattens at its close.
    """
    n = len(opens)
    if qty < 1 or entry_index < 0 or last_index < 0 or entry_index >= n or last_index >= n:
        return TradeResult(False)
    if side not in ("long", "short") or kind not in ("market", "stop", "limit"):
        return TradeResult(False)
    if exit_on not in ("open", "close"):
        return TradeResult(False)
    scan_end = last_index if exit_on == "open" else last_index + 1
    if kind == "market" and entry_index >= scan_end:
        return TradeResult(False)

    filled = _seek_entry(
        opens, highs, lows, side=side, entry_index=entry_index, kind=kind, level=level, scan_end=scan_end, tick=tick
    )
    if filled is None:
        return TradeResult(False)
    fill_i, entry = filled
    fee = fee_rt * qty
    worst = entry
    for i in range(fill_i, scan_end):
        stopped, stop_price = _stop_fill(opens[i], highs[i], lows[i], side, stop, tick)
        if stopped:
            return _finish(
                side, entry, stop_price, "stop", qty, fee, point_value, worst, fill_i, i
            )
        targeted, target_price = _target_fill(opens[i], highs[i], lows[i], side, target)
        if targeted:
            return _finish(
                side, entry, target_price, "target", qty, fee, point_value, worst, fill_i, i
            )
        if side == "long":
            worst = min(worst, lows[i])
        else:
            worst = max(worst, highs[i])
    if exit_on == "open":
        raw = opens[last_index]
    else:
        raw = closes[last_index]
    exit_price = _slip("sell" if side == "long" else "buy", raw, tick)
    return _finish(
        side, entry, exit_price, "time", qty, fee, point_value, worst, fill_i, last_index
    )


def _seek_entry(opens, highs, lows, *, side, entry_index, kind, level, scan_end, tick):
    order = "buy" if side == "long" else "sell"
    if kind == "market":
        return entry_index, _slip(order, opens[entry_index], tick)
    if level is None:
        return None
    for i in range(entry_index, scan_end):
        if kind == "stop":
            hit = highs[i] >= level - 1e-9 if side == "long" else lows[i] <= level + 1e-9
            if not hit:
                continue
            if side == "long":
                raw = opens[i] if opens[i] >= level - 1e-9 else level
            else:
                raw = opens[i] if opens[i] <= level + 1e-9 else level
            return i, _slip(order, raw, tick)
        traded = lows[i] < level - 1e-9 if side == "long" else highs[i] > level + 1e-9
        if not traded:
            continue
        if side == "long":
            price = opens[i] if opens[i] < level - 1e-9 else level
        else:
            price = opens[i] if opens[i] > level + 1e-9 else level
        return i, price
    return None


def _stop_fill(open_, high, low, side, stop, tick):
    if stop is None:
        return False, 0.0
    if side == "long":
        if low > stop + 1e-9:
            return False, 0.0
        raw = open_ if open_ <= stop + 1e-9 else stop
        return True, _slip("sell", raw, tick)
    if high < stop - 1e-9:
        return False, 0.0
    raw = open_ if open_ >= stop - 1e-9 else stop
    return True, _slip("buy", raw, tick)


def _target_fill(open_, high, low, side, target):
    if target is None:
        return False, 0.0
    if side == "long":
        if high <= target + 1e-9:
            return False, 0.0
        price = open_ if open_ > target + 1e-9 else target
        return True, price
    if low >= target - 1e-9:
        return False, 0.0
    price = open_ if open_ < target - 1e-9 else target
    return True, price


def _slip(order_side: str, price: float, tick: float) -> float:
    if order_side == "buy":
        return price + tick
    return price - tick


def _finish(side, entry, exit_price, reason, qty, fee, point_value, worst, fill_i, exit_i) -> TradeResult:
    if side == "long":
        points = exit_price - entry
        worst_price = min(worst, exit_price)
        mae_points = max(0.0, entry - worst_price)
    else:
        points = entry - exit_price
        worst_price = max(worst, exit_price)
        mae_points = max(0.0, worst_price - entry)
    pnl = points * point_value * qty - fee
    mae = mae_points * point_value * qty + fee
    return TradeResult(True, entry, exit_price, reason, pnl, mae, fill_i, exit_i)

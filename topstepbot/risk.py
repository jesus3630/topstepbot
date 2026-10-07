"""Daily loss, profit stop, trade count, and consecutive-loss gates."""

from __future__ import annotations

from dataclasses import dataclass

from topstepbot.config import RiskConfig
from topstepbot.models import DayStats


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    risk_multiplier: float = 1.0


def mark_to_market(stats: DayStats) -> float:
    return stats.realized + stats.unrealized


def distance_to_mll(equity: float, mll_floor: float) -> float:
    return equity - mll_floor


def evaluate_entry(
    stats: DayStats,
    new_trade_risk: float,
    risk: RiskConfig,
    *,
    equity: float,
    mll_floor: float,
) -> RiskDecision:
    """Decide whether a new trade may be sent.

    `new_trade_risk` and `stats.open_risk` are positive dollar amounts that
    could still be lost. Signed P&L uses negative numbers for losses.
    The pre-trade guard is: realized - open_risk - new_risk stays above
    -daily_max_loss (rulebook section 4.4).
    """
    if stats.halted:
        return RiskDecision(False, stats.halt_reason or "halted")
    if stats.trades >= risk.max_trades_per_day:
        return RiskDecision(False, "max trades for the day")
    if stats.consecutive_losses >= risk.max_consecutive_losses:
        return RiskDecision(False, "consecutive-loss stop")

    mtm = mark_to_market(stats)
    if mtm <= -risk.daily_max_loss:
        return RiskDecision(False, "daily max loss")
    if mtm >= risk.daily_profit_stop:
        return RiskDecision(False, "daily profit stop")

    cushion = distance_to_mll(equity, mll_floor)
    if cushion <= risk.mll_stop_multiple * risk.risk_per_trade:
        return RiskDecision(False, "too close to the Topstep max-loss floor")

    multiplier = 1.0
    if cushion <= risk.mll_halve_risk_multiple * risk.risk_per_trade:
        multiplier = 0.5

    projected = stats.realized - stats.open_risk - new_trade_risk
    if projected < -risk.daily_max_loss:
        return RiskDecision(False, "new trade would break the daily loss budget")
    return RiskDecision(True, "ok", multiplier)


def update_halt_from_pnl(stats: DayStats, risk: RiskConfig) -> str | None:
    """Flatten-and-stop reasons based on marked P&L. Returns a reason or None."""
    mtm = mark_to_market(stats)
    if mtm <= -risk.daily_max_loss:
        stats.halted = True
        stats.halt_reason = "daily max loss"
        return stats.halt_reason
    if mtm >= risk.daily_profit_stop:
        stats.halted = True
        stats.halt_reason = "daily profit stop"
        return stats.halt_reason
    return None


def record_closed_trade(stats: DayStats, pnl: float, risk: RiskConfig) -> None:
    stats.trades += 1
    stats.realized += pnl
    if pnl < 0:
        stats.consecutive_losses += 1
    elif pnl > 0:
        stats.consecutive_losses = 0
    if stats.consecutive_losses >= risk.max_consecutive_losses:
        stats.halted = True
        stats.halt_reason = "consecutive-loss stop"
    if stats.trades >= risk.max_trades_per_day:
        stats.halted = True
        stats.halt_reason = stats.halt_reason or "max trades for the day"
    update_halt_from_pnl(stats, risk)


def initial_mll_floor(starting_balance: float, topstep_max_loss: float) -> float:
    return starting_balance - topstep_max_loss


def trail_mll_floor(previous_floor: float, end_of_day_equity: float, topstep_max_loss: float) -> float:
    """The floor trails up with the end-of-day balance and never moves down."""
    trailed = end_of_day_equity - topstep_max_loss
    return max(previous_floor, trailed)

"""Backtest statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from topstepbot.config import BotConfig
from topstepbot.models import ClosedTrade
from topstepbot.strategy.engine import SessionResult


@dataclass(frozen=True)
class BacktestReport:
    trades: int
    wins: int
    losses: int
    win_rate: float
    average_r: float
    net_pnl: float
    max_drawdown: float
    worst_day: date | None
    worst_day_pnl: float
    days_reaching_daily_stop: list[date]
    days_worse_than_daily_stop: list[date]
    days_through_topstep_max_loss: list[date]
    ending_equity: float
    skips: dict[str, int]

    def text(self, config: BotConfig) -> str:
        worst = "n/a" if self.worst_day is None else f"{self.worst_day} ${self.worst_day_pnl:,.2f}"
        win_pct = self.win_rate * 100
        lines = [
            "Backtest report",
            f"Trades: {self.trades}  (wins {self.wins}, losses {self.losses})",
            f"Win rate: {win_pct:.1f}%",
            f"Average R: {self.average_r:.2f}",
            f"Net P&L: ${self.net_pnl:,.2f}",
            f"Max drawdown: ${self.max_drawdown:,.2f}",
            f"Worst day: {worst}",
            f"Ending equity: ${self.ending_equity:,.2f}",
            (
                f"Days that reached the ${config.risk.daily_max_loss:,.0f} daily stop: "
                f"{_dates(self.days_reaching_daily_stop)}"
            ),
            (
                f"Days worse than the ${config.risk.daily_max_loss:,.0f} daily stop: "
                f"{_dates(self.days_worse_than_daily_stop)}"
            ),
            (
                f"Days that touched the ${config.risk.topstep_max_loss:,.0f} Topstep max loss: "
                f"{_dates(self.days_through_topstep_max_loss)}"
            ),
        ]
        if self.skips:
            top = sorted(self.skips.items(), key=lambda item: item[1], reverse=True)[:8]
            summary = ", ".join(f"{name}={count}" for name, count in top)
            lines.append(f"Most common skips: {summary}")
        return "\n".join(lines)


def build_report(result: SessionResult, config: BotConfig) -> BacktestReport:
    trades = result.trades
    wins = [trade for trade in trades if trade.pnl > 0]
    losses = [trade for trade in trades if trade.pnl < 0]
    average_r = sum(trade.r_multiple for trade in trades) / len(trades) if trades else 0.0
    net = sum(trade.pnl for trade in trades)
    worst_day, worst_pnl = _worst_day(result.daily_pnl)
    reaching, worse = _daily_stop_days(result.daily_mtm_low, config.risk.daily_max_loss)
    mll_days = _mll_days(result.equity_curve, config)
    return BacktestReport(
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / len(trades)) if trades else 0.0,
        average_r=average_r,
        net_pnl=net,
        max_drawdown=_max_drawdown(result.equity_curve),
        worst_day=worst_day,
        worst_day_pnl=worst_pnl,
        days_reaching_daily_stop=reaching,
        days_worse_than_daily_stop=worse,
        days_through_topstep_max_loss=mll_days,
        ending_equity=result.ending_equity,
        skips=dict(result.skips),
    )


def _worst_day(daily: dict[date, float]) -> tuple[date | None, float]:
    if not daily:
        return None, 0.0
    day = min(daily, key=daily.get)
    return day, daily[day]


def _daily_stop_days(lows: dict[date, float], limit: float) -> tuple[list[date], list[date]]:
    reaching = sorted(day for day, low in lows.items() if low <= -limit)
    # A dollar past the stop counts as worse. The bot tries to flatten at the line.
    worse = sorted(day for day, low in lows.items() if low < -limit - 1.0)
    return reaching, worse


def _mll_days(curve: list[tuple], config: BotConfig) -> list[date]:
    from topstepbot.risk import initial_mll_floor, trail_mll_floor
    from topstepbot.timeutil import session_date

    floor = initial_mll_floor(config.account.starting_balance, config.risk.topstep_max_loss)
    breached: set[date] = set()
    current_day: date | None = None
    day_close_equity = config.account.starting_balance
    for moment, equity in curve:
        day = session_date(moment, config.session)
        if current_day is None:
            current_day = day
        elif day != current_day:
            floor = trail_mll_floor(floor, day_close_equity, config.risk.topstep_max_loss)
            current_day = day
        if equity <= floor:
            breached.add(day)
        day_close_equity = equity
    return sorted(breached)


def _max_drawdown(curve: list[tuple]) -> float:
    peak = None
    worst = 0.0
    for _moment, equity in curve:
        if peak is None or equity > peak:
            peak = equity
        if peak is not None:
            worst = max(worst, peak - equity)
    return worst


def _dates(days: list[date]) -> str:
    if not days:
        return "none"
    return ", ".join(day.isoformat() for day in days)

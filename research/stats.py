"""Trade summaries, selection gates, and multiple-testing checks.

These functions do not know the strategy list. The gates below are the ones
written down before the backtest: positive after costs on train and on
validation, with a similar win rate and no collapse in expectancy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np


@dataclass(frozen=True)
class Trade:
    name: str
    session: date
    side: str
    qty: int
    entry: float
    exit: float
    pnl: float
    mae: float
    reason: str


def empty_summary() -> dict:
    return {
        "trades": 0,
        "wins": 0,
        "win_rate": 0.0,
        "expectancy": 0.0,
        "net": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "max_dd": 0.0,
        "worst_day": 0.0,
        "best_day": 0.0,
        "avg_qty": 0.0,
        "profit_factor": None,
    }


def max_drawdown(pnls) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += float(pnl)
        if equity > peak:
            peak = equity
        drop = peak - equity
        if drop > worst:
            worst = drop
    return worst


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return empty_summary()
    pnls = [float(t.pnl) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    daily: dict[date, float] = {}
    for trade in trades:
        daily[trade.session] = daily.get(trade.session, 0.0) + float(trade.pnl)
    days = list(daily.values())
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": len(wins) / len(trades),
        "expectancy": sum(pnls) / len(trades),
        "net": sum(pnls),
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "max_dd": max_drawdown(pnls),
        "worst_day": min(days) if days else 0.0,
        "best_day": max(days) if days else 0.0,
        "avg_qty": sum(t.qty for t in trades) / len(trades),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
    }


def daily_vector(trades: list[Trade], sessions: list[date]) -> np.ndarray:
    """Closed P&L per session. Days with no trade are zero."""
    acc = dict.fromkeys(sessions, 0.0)
    for trade in trades:
        if trade.session in acc:
            acc[trade.session] += float(trade.pnl)
    return np.fromiter((acc[s] for s in sessions), dtype=float, count=len(sessions))


def train_ok(summary: dict) -> bool:
    return summary["trades"] >= 100 and summary["expectancy"] > 0 and summary["net"] > 0


def val_ok(train: dict, val: dict) -> bool:
    if val["trades"] < 40 or val["expectancy"] <= 0 or val["net"] <= 0:
        return False
    if abs(val["win_rate"] - train["win_rate"]) > 0.15:
        return False
    if val["expectancy"] < 0.25 * train["expectancy"]:
        return False
    return True


def failure_reasons(train: dict, val: dict) -> list[str]:
    reasons = []
    if train["trades"] < 100:
        reasons.append(f"train trades {train['trades']} < 100")
    if train["expectancy"] <= 0:
        reasons.append("train expectancy is not positive")
    if train["net"] <= 0:
        reasons.append("train net is not positive")
    if val["trades"] < 40:
        reasons.append(f"validation trades {val['trades']} < 40")
    if val["expectancy"] <= 0:
        reasons.append("validation expectancy is not positive")
    if val["net"] <= 0:
        reasons.append("validation net is not positive")
    if train["expectancy"] > 0 and abs(val["win_rate"] - train["win_rate"]) > 0.15:
        reasons.append("win rate moved by more than 15 percentage points")
    if train["expectancy"] > 0 and val["expectancy"] < 0.25 * train["expectancy"]:
        reasons.append("validation expectancy is under 25% of train")
    return reasons


def corr(left, right) -> float:
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if len(a) < 3 or float(a.std()) == 0.0 or float(b.std()) == 0.0:
        return 1.0
    value = float(np.corrcoef(a, b)[0, 1])
    if value != value:
        return 1.0
    return value


def bootstrap_p(daily, n: int = 2000, seed: int = 12345) -> float:
    """One-sided bootstrap p-value for a daily-mean edge of zero or less.

    p = (count of resampled means <= 0 + 1) / (n + 1).
    """
    arr = np.asarray(daily, dtype=float)
    if len(arr) == 0:
        return 1.0
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(arr), size=(n, len(arr)))
    means = arr[draws].mean(axis=1)
    return float((int(np.count_nonzero(means <= 0.0)) + 1) / (n + 1))


def signflip_p(daily, n: int = 2000, seed: int = 12345) -> float:
    """Fraction of sign-flipped daily means at least as large as the observed mean."""
    arr = np.asarray(daily, dtype=float)
    if len(arr) == 0:
        return 1.0
    observed = float(arr.mean())
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(n, len(arr)))
    means = (signs * arr).mean(axis=1)
    return float((int(np.count_nonzero(means >= observed - 1e-12)) + 1) / (n + 1))


def adjust(p_value: float, n_tests: int) -> float:
    return float(min(1.0, p_value * n_tests))

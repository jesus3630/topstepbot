from topstepbot.config import load_config
from topstepbot.models import DayStats
from topstepbot.risk import evaluate_entry, initial_mll_floor, record_closed_trade, update_halt_from_pnl

CONFIG = load_config("config/settings.yaml")
RISK = CONFIG.risk
FLOOR = initial_mll_floor(CONFIG.account.starting_balance, RISK.topstep_max_loss)


def _ok_stats(**kwargs) -> DayStats:
    stats = DayStats()
    for key, value in kwargs.items():
        setattr(stats, key, value)
    return stats


def test_new_trade_must_leave_the_daily_loss_budget_intact():
    stats = _ok_stats(realized=-250, open_risk=0)
    decision = evaluate_entry(stats, new_trade_risk=200, risk=RISK, equity=49750, mll_floor=FLOOR)
    assert not decision.allowed
    assert "daily loss" in decision.reason


def test_open_loss_counts_with_realized():
    stats = _ok_stats(realized=-100, unrealized=-350)
    reason = update_halt_from_pnl(stats, RISK)
    assert reason == "daily max loss"
    assert stats.halted


def test_daily_profit_stop():
    stats = _ok_stats(realized=800)
    reason = update_halt_from_pnl(stats, RISK)
    assert reason == "daily profit stop"


def test_max_trades_and_two_losses():
    stats = DayStats()
    record_closed_trade(stats, pnl=-50, risk=RISK)
    record_closed_trade(stats, pnl=-50, risk=RISK)
    assert stats.consecutive_losses == 2
    assert stats.halted
    decision = evaluate_entry(stats, new_trade_risk=100, risk=RISK, equity=49900, mll_floor=FLOOR)
    assert not decision.allowed

    fresh = DayStats(trades=3)
    decision = evaluate_entry(fresh, new_trade_risk=100, risk=RISK, equity=50000, mll_floor=FLOOR)
    assert not decision.allowed
    assert "max trades" in decision.reason


def test_a_winner_resets_the_loss_streak():
    stats = DayStats()
    record_closed_trade(stats, pnl=-10, risk=RISK)
    record_closed_trade(stats, pnl=20, risk=RISK)
    assert stats.consecutive_losses == 0
    assert not stats.halted

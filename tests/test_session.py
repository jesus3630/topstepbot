from datetime import datetime
from zoneinfo import ZoneInfo

from topstepbot.config import load_config
from topstepbot.models import Phase
from topstepbot.news import block_reason
from topstepbot.session import entries_allowed, must_flatten, phase_at

TZ = ZoneInfo("America/Chicago")
CONFIG = load_config("config/settings.yaml")


def at(hour, minute):
    return datetime(2026, 1, 6, hour, minute, tzinfo=TZ)


def test_agreed_settings_live_in_the_config_file():
    assert CONFIG.instrument.symbol == "MES"
    assert CONFIG.account.starting_balance == 50000
    assert CONFIG.risk.risk_per_trade == 200
    assert CONFIG.risk.daily_max_loss == 400
    assert CONFIG.risk.daily_profit_stop == 800
    assert CONFIG.risk.max_trades_per_day == 3
    assert CONFIG.risk.max_consecutive_losses == 2
    assert CONFIG.risk.max_contracts == 2
    assert CONFIG.exits.move_stop_to_breakeven_after_first_target is False
    assert CONFIG.session.entry_start.hour == 8 and CONFIG.session.entry_start.minute == 45
    assert CONFIG.session.entry_end.hour == 10 and CONFIG.session.entry_end.minute == 15
    assert CONFIG.session.flatten_time.hour == 10 and CONFIG.session.flatten_time.minute == 30
    assert CONFIG.runtime.armed is False
    assert CONFIG.news.no_trade_today is False


def test_session_phases():
    session = CONFIG.session
    assert phase_at(at(8, 40), session) is Phase.OPENING_RANGE
    assert not entries_allowed(at(8, 40), session)
    assert phase_at(at(9, 0), session) is Phase.ENTRY
    assert entries_allowed(at(9, 0), session)
    assert entries_allowed(at(10, 15), session)
    assert phase_at(at(10, 20), session) is Phase.MANAGE
    assert not entries_allowed(at(10, 20), session)
    assert must_flatten(at(10, 30), session)
    assert must_flatten(at(15, 10), session)
    assert must_flatten(at(16, 0), session)
    # Globex evening belongs to the next session and is not a flatten.
    assert phase_at(at(18, 0), session) is Phase.BEFORE
    assert not must_flatten(at(18, 0), session)


def test_blackout_date_and_manual_switch(tmp_path):
    news = CONFIG.news
    assert block_reason(at(9, 0), news, "America/Chicago") is None
    blocked = type(news)(
        no_trade_today=True,
        blackout_dates=news.blackout_dates,
        intraday_blackouts=news.intraday_blackouts,
    )
    assert block_reason(at(9, 0), blocked, "America/Chicago") == "no_trade_today switch is on"
    dated = type(news)(
        no_trade_today=False,
        blackout_dates=("2026-01-06",),
        intraday_blackouts=(),
    )
    assert "blackout date" in block_reason(at(9, 0), dated, "America/Chicago")

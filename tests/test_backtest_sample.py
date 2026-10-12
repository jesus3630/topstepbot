from topstepbot.backtest.runner import run_backtest
from topstepbot.bars import write_bars
from topstepbot.config import load_config
from topstepbot.sampledata import build_synthetic_bars


def test_synthetic_sample_backtest_takes_a_setup_a_trade(tmp_path):
    csv_path = tmp_path / "sample_mes_1m_synthetic.csv"
    write_bars(
        csv_path,
        build_synthetic_bars(),
        comment="SYNTHETIC DATA. Not real market prices. For the backtester demo only.",
    )
    config = load_config("config/settings.yaml")
    report = run_backtest(config, csv_path, journal=_quiet_journal(tmp_path))
    assert report.trades >= 1
    assert report.wins >= 1
    assert report.days_worse_than_daily_stop == []
    assert report.days_through_topstep_max_loss == []
    text = report.text(config)
    assert "Win rate" in text
    assert "Average R" in text
    assert "Max drawdown" in text
    assert "Worst day" in text
    assert "daily stop" in text
    assert "Topstep max loss" in text


def _quiet_journal(tmp_path):
    from topstepbot.journal import Journal

    journal = Journal(tmp_path / "logs")
    journal.logger.handlers = [handler for handler in journal.logger.handlers if handler.__class__.__name__ != "StreamHandler"]
    return journal

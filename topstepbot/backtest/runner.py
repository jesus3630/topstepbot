"""Replay 1-minute bars through StrategyEngine and the paper broker."""

from __future__ import annotations

from pathlib import Path

from topstepbot.backtest.report import BacktestReport, build_report
from topstepbot.bars import load_bars
from topstepbot.broker.paper import PaperBroker
from topstepbot.config import BotConfig
from topstepbot.journal import Journal
from topstepbot.strategy.engine import StrategyEngine


def run_backtest(config: BotConfig, csv_path: str | Path, journal: Journal | None = None) -> BacktestReport:
    bars = load_bars(csv_path, config.session.timezone)
    if not bars:
        raise ValueError(f"No bars in {csv_path}")
    log = journal or Journal(config.runtime.log_dir, clock=bars[0].time)
    log.info(f"Backtest starting bars={len(bars)} file={csv_path}")
    broker = PaperBroker(config)
    engine = StrategyEngine(config, broker, log, armed=True)
    for bar in bars:
        engine.on_minute(bar)
    result = engine.finish()
    report = build_report(result, config)
    log.info(report.text(config).replace("\n", " | "))
    return report

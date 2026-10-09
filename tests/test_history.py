"""Read-only history download and the report that reads it."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from topstepbot.broker.projectx import ProjectXError
from topstepbot.broker.ratelimit import SlidingWindowLimiter
from topstepbot.broker.readonly import ReadOnlyViolation
from topstepbot.historyfetch import fetch_history, quarter_search_texts
from topstepbot.historyreport import (
    load_labeled_bars,
    parse_contract_expiry,
    stitch_front_month,
    write_history_report,
    _LabeledBar,
)
from topstepbot.models import Bar, Side
from topstepbot.strategy.engine import PendingBreak, RuleStudy
from topstepbot.timeutil import CHICAGO


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _ReadOnlyFake:
    def __init__(self, bars_for, fail_bars: int = 0, retry_after: float | None = None) -> None:
        self.bars_for = bars_for
        self.fail_bars = fail_bars
        self.last_retry_after = retry_after
        self.calls: list[tuple] = []
        self.place_calls = 0

    def place_order(self, payload=None):
        self.place_calls += 1
        raise ReadOnlyViolation("disabled")

    def search_contracts_raw(self, text, live=False):
        self.calls.append(("search", text, live))
        if text == "MES":
            return {"contracts": [{"id": "CON.F.US.MES.Z26", "name": "MESZ6"}]}
        return {"contracts": []}

    def retrieve_bars_raw(self, contract_id, start, end, **kwargs):
        self.calls.append(("bars", start, end, kwargs))
        if self.fail_bars:
            self.fail_bars -= 1
            raise ProjectXError("ProjectX rate limit (HTTP 429). Back off and try again.")
        return self.bars_for(start, end)


def _bar_payload(moment: datetime) -> dict:
    return {
        "t": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "o": 100.0,
        "h": 101.0,
        "l": 99.5,
        "c": 100.25,
        "v": 12,
    }


def _one_bar(start, end):
    stamp = datetime(start.year, start.month, start.day, 14, 30, tzinfo=timezone.utc)
    # `start` is Central. Compare in absolute time.
    if stamp < start.astimezone(timezone.utc) or stamp >= end.astimezone(timezone.utc):
        local = start.astimezone(CHICAGO)
        stamp = datetime(local.year, local.month, local.day, 14, 30, tzinfo=timezone.utc)
    if not (start <= stamp.astimezone(CHICAGO) < end):
        return {"bars": []}
    return {"bars": [_bar_payload(stamp), _bar_payload(stamp)]}


def _maybe_capped(start, end):
    local = start.astimezone(CHICAGO)
    if end - start > timedelta(hours=12):
        first = datetime(local.year, local.month, local.day, 15, 0, tzinfo=timezone.utc)
        second = datetime(local.year, local.month, local.day, 16, 0, tzinfo=timezone.utc)
        return {"bars": [_bar_payload(first), _bar_payload(second)]}
    stamp = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return {"bars": [_bar_payload(stamp)]}


def _run_fetch(tmp_path, client, **kwargs):
    notes: list[str] = []
    clock = kwargs.pop("clock", _Clock())
    limiter = SlidingWindowLimiter(clock=clock, sleep=clock.sleep, jitter=lambda: 0.0)
    count = fetch_history(
        days=kwargs.get("days", 1),
        out=tmp_path / "mes.csv",
        client=client,
        say=notes.append,
        limiter=limiter,
        sleep=clock.sleep,
        now=kwargs.get("now", datetime(2026, 1, 6, 12, 0, tzinfo=CHICAGO)),
    )
    return count, notes, clock


def test_fetch_refuses_a_client_that_can_place_orders(tmp_path):
    class CanOrder:
        def __init__(self) -> None:
            self.http = 0

        def place_order(self, payload=None):
            return 1

        def search_contracts_raw(self, *args, **kwargs):
            self.http += 1
            return {"contracts": []}

    client = CanOrder()
    with pytest.raises(SystemExit, match="can place orders"):
        fetch_history(
            days=1,
            out=tmp_path / "mes.csv",
            client=client,
            now=datetime(2026, 1, 6, 12, 0, tzinfo=CHICAGO),
        )
    assert client.http == 0


def test_fetch_writes_ct_bars_deduped_with_contract(tmp_path, monkeypatch):
    monkeypatch.setattr("topstepbot.historyfetch.BAR_LIMIT", 2)
    client = _ReadOnlyFake(_maybe_capped)
    count, notes, _clock = _run_fetch(tmp_path, client)
    text = (tmp_path / "mes.csv").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "timestamp,open,high,low,close,volume,contract"
    assert "MESZ6" in text
    assert "-06:00" in text
    assert count == len(text.splitlines()) - 1
    assert any("split in half" in line for line in notes)
    bar_calls = [call for call in client.calls if call[0] == "bars"]
    assert bar_calls
    kwargs = bar_calls[0][3]
    assert kwargs["unit"] == 2
    assert kwargs["unit_number"] == 1
    assert kwargs["live"] is False
    stamps = [line.split(",")[0] for line in text.splitlines()[1:]]
    assert len(stamps) == len(set(stamps))


def test_fetch_resumes_completed_days_and_refreshes_the_last_day(tmp_path):
    destination = tmp_path / "mes.csv"
    destination.write_text(
        "timestamp,open,high,low,close,volume,contract\n"
        "2026-01-05T08:30:00-06:00,100.0000,101.0000,99.5000,100.2500,12,MESZ6\n",
        encoding="utf-8",
    )
    client = _ReadOnlyFake(_one_bar)
    _run_fetch(tmp_path, client, now=datetime(2026, 1, 7, 12, 0, tzinfo=CHICAGO), days=2)
    requested = [call[1].astimezone(CHICAGO).date() for call in client.calls if call[0] == "bars"]
    assert datetime(2026, 1, 5).date() not in requested
    assert datetime(2026, 1, 6).date() in requested
    assert datetime(2026, 1, 7).date() in requested
    rows = load_labeled_bars(destination)
    assert len(rows) >= 2
    assert all(item.contract == "MESZ6" for item in rows)


def test_fetch_backs_off_on_429_using_retry_after(tmp_path):
    client = _ReadOnlyFake(_one_bar, fail_bars=1, retry_after=10)
    _count, notes, clock = _run_fetch(tmp_path, client)
    assert clock.slept
    assert max(clock.slept) >= 10
    assert any("pause" in line for line in notes)


def test_quarter_search_includes_expired_codes():
    texts = quarter_search_texts(
        datetime(2025, 10, 9, tzinfo=CHICAGO),
        datetime(2026, 10, 9, tzinfo=CHICAGO),
    )
    assert texts[0] == "MES"
    assert "MESU26" in texts
    assert "MESU6" in texts
    assert "MESM26" in texts
    assert "MESZ25" in texts or "MESZ5" in texts


def test_historyfetch_source_cannot_name_an_order_route():
    text = Path("topstepbot/historyfetch.py").read_text(encoding="utf-8")
    assert "import ProjectXClient" not in text
    assert "ProjectXBroker" not in text
    assert "/api/Order" not in text
    assert "ReadOnlyProjectXClient" in text


def test_contract_expiry_and_front_month_stitch():
    assert parse_contract_expiry("MESZ6", 2026) == datetime(2026, 12, 15).date()
    assert parse_contract_expiry("MESU26", 2026) == datetime(2026, 9, 15).date()
    assert parse_contract_expiry("CON.F.US.MES.Z26", 2026) == datetime(2026, 12, 15).date()
    assert parse_contract_expiry("ES=F", 2026) is None
    august = datetime(2026, 8, 10, 9, 0, tzinfo=CHICAGO)
    october = datetime(2026, 10, 10, 9, 0, tzinfo=CHICAGO)

    def labeled(moment, close, contract):
        return _LabeledBar(
            Bar(time=moment, open=close, high=close + 1, low=close - 1, close=close, volume=10),
            contract,
        )

    bars, note = stitch_front_month(
        [
            labeled(august, 10, "MESU6"),
            labeled(august, 20, "MESZ6"),
            labeled(october, 30, "MESU6"),
            labeled(october, 40, "MESZ6"),
        ]
    )
    assert [bar.close for bar in bars] == [10, 40]
    assert "front month" in note
    assert "Roll" in note


def test_history_report_states_combine_stats_without_editing_settings(tmp_path):
    from topstepbot.bars import write_bars
    from topstepbot.config import load_config
    from topstepbot.sampledata import build_synthetic_bars

    settings = Path("config/settings.yaml")
    before = settings.read_text(encoding="utf-8")
    csv_path = tmp_path / "bars.csv"
    write_bars(csv_path, build_synthetic_bars())
    config = load_config(settings)
    text = write_history_report(
        config,
        csv_path,
        out=tmp_path / "report.txt",
        log_dir=tmp_path / "logs",
    )
    assert settings.read_text(encoding="utf-8") == before
    assert config.filters.chop_ema_crosses == 4
    for phrase in (
        "In-sample",
        "Out-of-sample",
        "Win rate",
        "Average win",
        "Average loss",
        "Expectancy per trade",
        "Max drawdown",
        "2,000",
        "1,000",
        "3,000",
        "40%",
        "Per day",
        "Chop",
        "VWAP",
        "ATR stretch",
        "Volume",
        "Retest",
        "Fakeout",
        "10:15",
        "How MES moves 8:30-10:30 CT",
        "opening range",
        "slippage",
        "Live defaults were not changed",
    ):
        assert phrase in text
    assert (tmp_path / "report.txt").read_text(encoding="utf-8").startswith("History report")


def test_retest_audit_counts_a_pullback_deeper_than_two_ticks(tmp_path):
    from topstepbot.broker.paper import PaperBroker
    from topstepbot.config import load_config
    from topstepbot.journal import Journal
    from topstepbot.strategy.engine import StrategyEngine

    config = load_config("config/settings.yaml")
    journal = Journal(tmp_path / "logs")
    journal.logger.handlers = [
        handler for handler in journal.logger.handlers if handler.__class__.__name__ != "StreamHandler"
    ]
    engine = StrategyEngine(config, PaperBroker(config), journal, armed=True)
    engine.study = RuleStudy(audit=True)
    deep = Bar(
        time=datetime(2026, 1, 6, 9, 0, tzinfo=CHICAGO),
        open=100,
        high=101,
        low=99.25,
        close=100.25,
        volume=10,
    )
    engine._audit_retest_depth(deep, PendingBreak("A", Side.LONG, "or_high", 100.0, 0))
    assert engine.study.blocks[0]["filter"] == "retest"
    assert engine.study.blocks[0]["detail"] == "or_high:3.0"
    shallow = Bar(
        time=datetime(2026, 1, 6, 9, 5, tzinfo=CHICAGO),
        open=100,
        high=101,
        low=99.5,
        close=100.25,
        volume=10,
    )
    engine._audit_retest_depth(shallow, PendingBreak("A", Side.LONG, "or_high", 100.0, 1))
    assert len(engine.study.blocks) == 1


def test_indicator_cache_matches_a_full_recompute(tmp_path):
    from topstepbot.broker.paper import PaperBroker
    from topstepbot.config import load_config
    from topstepbot.indicators import atr, ema, swing_points, trend_for
    from topstepbot.journal import Journal
    from topstepbot.sampledata import build_synthetic_bars
    from topstepbot.strategy.engine import StrategyEngine

    config = load_config("config/settings.yaml")
    journal = Journal(tmp_path / "logs")
    journal.logger.handlers = [
        handler for handler in journal.logger.handlers if handler.__class__.__name__ != "StreamHandler"
    ]
    engine = StrategyEngine(config, PaperBroker(config), journal, armed=True)
    wing = config.filters.swing_bars_each_side
    for bar in build_synthetic_bars():
        engine.on_minute(bar)
        if not engine.bars_5:
            continue
        engine._ensure_indicators()
        closes = [item.close for item in engine.bars_5]
        assert engine._ema == ema(closes, config.filters.ema_period)
        assert engine._atr == atr(engine.bars_5, config.filters.atr_period)
        assert engine._swing_high == swing_points(engine.bars_5, wing, "high")
        assert engine._swing_low == swing_points(engine.bars_5, wing, "low")
        for side in (Side.LONG, Side.SHORT):
            assert engine._trend_ok(side) is trend_for(engine.bars_5, wing, side)
        if engine.session_day is None:
            continue
        from topstepbot.levels import build_level_map, london_hl, overnight_hl, prior_rth_hlc

        session = config.session
        engine._rebuild_levels(bar.close)
        expected = build_level_map(
            prior=prior_rth_hlc(
                engine.minutes, engine.session_day, session.timezone, session.rth_open, session.prior_rth_end
            ),
            overnight=overnight_hl(
                engine.minutes, engine.session_day, session.timezone, session.vwap_anchor, session.rth_open
            ),
            london=london_hl(engine.minutes, engine.session_day, session.timezone, session.rth_open),
            opening=engine.opening,
            vwap=engine.vwap,
            round_step=config.filters.round_number_points,
            near_price=bar.close,
        )
        assert [(level.name, level.price) for level in engine.levels] == [
            (level.name, level.price) for level in expected
        ]


def test_wider_retest_audit_counts_a_near_miss(tmp_path):
    from topstepbot.broker.paper import PaperBroker
    from topstepbot.config import load_config
    from topstepbot.journal import Journal
    from topstepbot.strategy.engine import StrategyEngine

    config = load_config("config/settings.yaml")
    journal = Journal(tmp_path / "logs")
    journal.logger.handlers = [
        handler for handler in journal.logger.handlers if handler.__class__.__name__ != "StreamHandler"
    ]
    engine = StrategyEngine(config, PaperBroker(config), journal, armed=True)
    engine.study = RuleStudy(audit=True, audit_retest_ticks=8)
    near = Bar(
        time=datetime(2026, 1, 6, 9, 5, tzinfo=CHICAGO),
        open=101.0,
        high=101.5,
        low=100.75,
        close=101.0,
        volume=10,
    )
    engine._audit_retest_depth(near, PendingBreak("A", Side.LONG, "or_high", 100.0, 0))
    assert engine.study.blocks[0]["detail"] == "or_high:-3.0"


def test_cli_offers_fetch_history():
    from topstepbot.cli import main

    with pytest.raises(SystemExit) as caught:
        main(["fetch-history", "--help"])
    assert caught.value.code == 0

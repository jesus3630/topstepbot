"""Databento history download and roll handling. No network, no API key."""

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from topstepbot.broker.paper import PaperBroker
from topstepbot.config import load_config
from topstepbot.databentofetch import (
    SYMBOL,
    contract_label,
    fetch_databento,
    raw_symbol_on,
    scale_price,
    usd_text,
)
from topstepbot.journal import Journal
from topstepbot.models import Bar, BracketLeg, Side
from topstepbot.strategy.engine import StrategyEngine

UTC = timezone.utc
KEY = "db-SUPERSECRETKEY99"


class FakeDatabento:
    def __init__(self, cost=5.5, rows=None, bounds=(None, None), boom=False):
        self.cost_value = cost
        self.rows = rows or []
        self.bounds = bounds
        self.boom = boom
        self.calls = []
        self.api_key = KEY

    def dataset_bounds(self, schema="ohlcv-1m"):
        self.calls.append(("bounds", schema))
        return self.bounds

    def cost(self, start, end, symbol, schema="ohlcv-1m"):
        self.calls.append(("cost", start, end, symbol, schema))
        if self.boom:
            raise RuntimeError(f"rejected {self.api_key}")
        return self.cost_value

    def bars(self, start, end, symbol, schema="ohlcv-1m"):
        self.calls.append(("bars", start, end, symbol, schema))
        return list(self.rows)

    def records(self, start, end, symbol, schema="ohlcv-1m"):
        self.calls.append(("records", start, end, symbol, schema))
        return list(self.rows)


def _bar(moment, price, contract, high=None, low=None):
    return Bar(
        time=moment,
        open=price,
        high=price if high is None else high,
        low=price if low is None else low,
        close=price,
        volume=10,
        contract=contract,
    )


def _engine(tmp_path):
    config = load_config("config/settings.yaml")
    journal = Journal(tmp_path / "logs")
    journal.logger.handlers = [
        handler for handler in journal.logger.handlers if not isinstance(handler, logging.StreamHandler)
    ]
    broker = PaperBroker(config)
    return StrategyEngine(config, broker, journal, armed=True)


def _kinds(client):
    return [call[0] if isinstance(call, tuple) else call for call in client.calls]


def test_scale_price_and_contract_label():
    assert scale_price(5000.25) == pytest.approx(5000.25)
    assert scale_price(5_000_250_000_000) == pytest.approx(5000.25)
    assert scale_price(2**63 - 1) is None
    assert contract_label("MESU4", 42001234) == "MESU4:42001234"
    assert contract_label("", 42) == "id:42"
    resolved = {"result": {"42": [{"d0": "2019-06-01", "d1": "2019-09-15", "s": "MESU9"}]}}
    june = datetime(2019, 6, 2, tzinfo=UTC)
    assert raw_symbol_on(resolved, 42, june) == "MESU9"
    assert raw_symbol_on(resolved, 42, datetime(2019, 9, 15, tzinfo=UTC)) == ""
    assert usd_text(12.5) == "12.50"
    assert usd_text(12.345) == "12.345"


def test_cost_above_the_cap_downloads_nothing(tmp_path):
    client = FakeDatabento(cost=25)
    lines = []
    with pytest.raises(SystemExit) as caught:
        fetch_databento(
            start="2019-05-01",
            end="2019-05-03",
            out=tmp_path / "mes.csv",
            max_cost=20,
            client=client,
            say=lines.append,
        )
    text = " ".join(lines) + " " + str(caught.value)
    assert "25.00 US dollars" in text
    assert "Nothing was downloaded" in text
    assert "bars" not in _kinds(client)
    assert KEY not in text
    assert not (tmp_path / "mes.csv").exists()


def test_yes_and_a_cheap_quote_download_and_scale_prices(tmp_path):
    moment = datetime(2019, 5, 1, 14, 30, tzinfo=UTC)
    client = FakeDatabento(
        cost=20,
        rows=[
            (moment, 5_000_250_000_000, 5_001_250_000_000, 5_000_000_000_000, 5_000_500_000_000, 11, 42, "MESM9"),
        ],
    )
    lines = []
    count = fetch_databento(
        start="2019-05-01",
        end="2019-05-03",
        out=tmp_path / "mes.csv",
        client=client,
        say=lines.append,
    )
    assert count == 1
    assert "bars" in _kinds(client)
    assert any(call[3] == SYMBOL for call in client.calls if isinstance(call, tuple) and call[0] == "cost")
    text = (tmp_path / "mes.csv").read_text(encoding="utf-8")
    assert KEY not in text
    assert KEY not in " ".join(lines)
    with (tmp_path / "mes.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["timestamp"] == "2019-05-01T09:30:00-05:00"
    assert float(row["open"]) == pytest.approx(5000.25)
    assert float(row["high"]) == pytest.approx(5001.25)
    assert float(row["low"]) == pytest.approx(5000.0)
    assert float(row["close"]) == pytest.approx(5000.5)
    assert row["volume"] == "11"
    assert row["contract"] == "MESM9:42"
    assert "cannot place" in " ".join(lines).lower()

    pricey = FakeDatabento(cost=25, rows=[(moment, 5000, 5001, 4999, 5000.5, 1, 7, "MESM9")])
    spoken = []
    fetch_databento(
        start="2019-05-01",
        end="2019-05-03",
        out=tmp_path / "yes.csv",
        max_cost=20,
        yes=True,
        client=pricey,
        say=spoken.append,
    )
    assert "bars" in _kinds(pricey)
    assert "--yes" in " ".join(spoken)
    assert KEY not in " ".join(spoken)


def test_a_leaked_key_in_an_error_is_redacted(tmp_path):
    client = FakeDatabento(boom=True)
    lines = []
    with pytest.raises(SystemExit) as caught:
        fetch_databento(
            start="2019-05-01",
            end="2019-05-03",
            out=tmp_path / "mes.csv",
            client=client,
            say=lines.append,
        )
    text = " ".join(lines) + " " + str(caught.value)
    assert KEY not in text
    assert "[REDACTED]" in text
    assert "bars" not in _kinds(client)


def test_resume_prices_only_the_remainder_and_clamps_to_availability(tmp_path):
    path = tmp_path / "mes.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume,contract\n"
        "2019-05-01T09:30:00-05:00,100,101,99,100,1,MESM9:1\n",
        encoding="utf-8",
    )
    later = datetime(2019, 5, 1, 15, 0, tzinfo=UTC)
    client = FakeDatabento(cost=1.25, rows=[(later, 101, 102, 100, 101.5, 2, 1, "MESM9")])
    lines = []
    count = fetch_databento(
        start="2019-05-01",
        end="2019-05-03",
        out=path,
        client=client,
        say=lines.append,
    )
    assert count == 2
    cost_call = next(call for call in client.calls if isinstance(call, tuple) and call[0] == "cost")
    assert cost_call[1] == datetime(2019, 5, 1, 14, 31, tzinfo=UTC)
    assert "remaining span" in " ".join(lines)
    assert KEY not in " ".join(lines)

    done = tmp_path / "done.csv"
    done.write_text(
        "timestamp,open,high,low,close,volume,contract\n"
        "2019-05-01T18:59:00-05:00,100,101,99,100,1,MESM9:1\n",
        encoding="utf-8",
    )
    finished = FakeDatabento(cost=9)
    spoken = []
    assert (
        fetch_databento(start="2019-05-01", end="2019-05-02", out=done, client=finished, say=spoken.append)
        == 1
    )
    assert "cost" not in _kinds(finished)
    assert "bars" not in _kinds(finished)
    assert "already covers" in " ".join(spoken)

    clamped = FakeDatabento(
        cost=2,
        bounds=(datetime(2019, 5, 6, tzinfo=UTC), datetime(2019, 6, 1, tzinfo=UTC)),
        rows=[],
    )
    notes = []
    fetch_databento(
        start="2019-05-01",
        end="2019-07-01",
        out=tmp_path / "clamp.csv",
        client=clamped,
        say=notes.append,
    )
    cost_call = next(call for call in clamped.calls if isinstance(call, tuple) and call[0] == "cost")
    assert cost_call[1] == datetime(2019, 5, 6, tzinfo=UTC)
    assert cost_call[2] == datetime(2019, 6, 1, tzinfo=UTC)
    assert "available from" in " ".join(notes)


def test_mnq_symbol_is_what_cost_and_bars_receive(tmp_path):
    moment = datetime(2019, 5, 1, 14, 30, tzinfo=UTC)
    client = FakeDatabento(
        cost=4,
        rows=[(moment, 18_000.25, 18_001.0, 17_999.5, 18_000.5, 9, 7, "MNQH4")],
    )
    lines = []
    count = fetch_databento(
        start="2019-05-01",
        end="2019-05-03",
        out=tmp_path / "mnq.csv",
        symbol=" MNQ.v.0 ",
        client=client,
        say=lines.append,
    )
    assert count == 1
    cost_call = next(call for call in client.calls if isinstance(call, tuple) and call[0] == "cost")
    bars_call = next(call for call in client.calls if isinstance(call, tuple) and call[0] == "bars")
    assert cost_call[3] == "MNQ.v.0"
    assert bars_call[3] == "MNQ.v.0"
    assert "MNQ.v.0" in " ".join(lines)
    with (tmp_path / "mnq.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["contract"] == "MNQH4:7"
    assert float(row["open"]) == pytest.approx(18000.25)
    with pytest.raises(SystemExit, match="empty"):
        fetch_databento(start="2019-05-01", symbol="  ", client=FakeDatabento(), out=tmp_path / "no.csv", say=lines.append)


def test_cli_symbol_defaults_to_mes_and_passes_mnq(monkeypatch, tmp_path):
    from topstepbot.cli import main

    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return 3

    monkeypatch.setattr("topstepbot.databentofetch.fetch_databento", fake)
    out = tmp_path / "mnq.csv"
    assert main(["fetch-databento", "--symbol", "MNQ.v.0", "--start", "2019-05-01", "--out", str(out)]) == 0
    assert seen["symbol"] == "MNQ.v.0"
    assert seen["start"] == "2019-05-01"
    assert seen["out"] == str(out)
    assert main(["fetch-databento", "--start", "2019-05-01", "--out", str(out)]) == 0
    assert seen["symbol"] == "MES.v.0"
    assert seen["schema"] == "ohlcv-1m"
    assert seen["quote_only"] is False
    assert seen["sessions_only"] is None
    with pytest.raises(SystemExit, match="empty"):
        main(["fetch-databento", "--symbol", "  ", "--start", "2019-05-01"])
    assert main(["fetch-databento", "--start", "2019-05-01", "--schema", "tbbo", "--quote-only", "--sessions-only"]) == 0
    assert seen["schema"] == "tbbo"
    assert seen["quote_only"] is True
    assert seen["sessions_only"] == "morning"
    assert main(["fetch-databento", "--start", "2019-05-01", "--schema", "tbbo", "--sessions-only", "rth"]) == 0
    assert seen["sessions_only"] == "rth"


def _trade(moment, side="B", price=5000.25, size=4, bid=5000.0, ask=5000.25):
    return {
        "ts_event": moment,
        "price": price,
        "size": size,
        "side": side,
        "bid_px": bid,
        "ask_px": ask,
        "bid_sz": 12,
        "ask_sz": 9,
        "raw_symbol": "MESM9",
        "instrument_id": 5,
        "action": "T",
    }


def test_session_windows_skip_weekends_and_follow_chicago_clock():
    from topstepbot.databentofetch import collapse_windows, session_windows

    morning = session_windows(datetime(2019, 5, 4, tzinfo=UTC), datetime(2019, 5, 8, tzinfo=UTC), "morning")
    assert [window[2].isoformat() for window in morning] == ["2019-05-06", "2019-05-07"]
    assert morning[0][0] == datetime(2019, 5, 6, 13, tzinfo=UTC)
    assert morning[0][1] == datetime(2019, 5, 6, 16, tzinfo=UTC)
    assert len(collapse_windows([(window[0], window[1]) for window in morning])) == 2

    winter = session_windows(datetime(2019, 1, 7, tzinfo=UTC), datetime(2019, 1, 8, tzinfo=UTC), "morning")
    assert winter[0][0] == datetime(2019, 1, 7, 14, tzinfo=UTC)
    assert winter[0][1] == datetime(2019, 1, 7, 17, tzinfo=UTC)

    cash = session_windows(datetime(2019, 5, 6, tzinfo=UTC), datetime(2019, 5, 7, tzinfo=UTC), "rth")
    assert cash[0][0] == datetime(2019, 5, 6, 13, 30, tzinfo=UTC)
    assert cash[0][1] == datetime(2019, 5, 6, 20, tzinfo=UTC)


def test_quote_only_sums_morning_windows_and_does_not_download(tmp_path):
    client = FakeDatabento(cost=1.25)
    lines = []
    out = tmp_path / "mes_1m_databento.csv"
    count = fetch_databento(
        start="2019-05-04",
        end="2019-05-08",
        out=out,
        schema="tbbo",
        quote_only=True,
        sessions_only="morning",
        max_cost=20,
        client=client,
        say=lines.append,
    )
    text = " ".join(lines)
    assert count == 0
    assert "2.50 US dollars" in text
    assert "sum of 2 metadata.get_cost" in text
    assert "does not spend credit" in text
    assert "Nothing was downloaded" in text
    assert KEY not in text
    assert "records" not in _kinds(client)
    assert "bars" not in _kinds(client)
    costs = [call for call in client.calls if isinstance(call, tuple) and call[0] == "cost"]
    assert costs[0][1] == datetime(2019, 5, 6, 13, tzinfo=UTC)
    assert costs[0][2] == datetime(2019, 5, 6, 16, tzinfo=UTC)
    assert costs[0][4] == "tbbo"
    assert not (tmp_path / "mes_tbbo_morning").exists()
    assert not out.exists()

    blocked = []
    pricey = FakeDatabento(cost=30)
    assert (
        fetch_databento(
            start="2019-05-06",
            end="2019-05-07",
            out=out,
            schema="tbbo",
            quote_only=True,
            sessions_only="morning",
            max_cost=20,
            client=pricey,
            say=blocked.append,
        )
        == 0
    )
    assert "above the 20.00" in " ".join(blocked)
    assert "records" not in _kinds(pricey)
    assert KEY not in " ".join(blocked)


def test_tick_days_are_resume_safe_and_keep_the_book(tmp_path):
    import gzip

    from topstepbot.databentofetch import normalize_tick_row

    assert normalize_tick_row({"ts_event": datetime(2019, 5, 6, 14, tzinfo=UTC), "price": 1, "size": 1, "side": "X"}, "trades") is None
    sell = normalize_tick_row(_trade(datetime(2019, 5, 6, 14, tzinfo=UTC), side=b"A"), "tbbo")
    assert sell["side"] == "A"
    assert sell["ts_event"].isoformat() == "2019-05-06T09:00:00-05:00"

    monday = datetime(2019, 5, 6, 14, tzinfo=UTC)
    tuesday = datetime(2019, 5, 7, 14, tzinfo=UTC)
    client = FakeDatabento(
        cost=2,
        rows=[
            _trade(monday, price=5_000_250_000_000),
            _trade(tuesday, side="A", price=5001),
            {"ts_event": monday, "price": 1, "size": 1, "side": "nope"},
        ],
    )
    lines = []
    directory = tmp_path / "flow"
    settings = Path("config/settings.yaml")
    before = settings.read_text(encoding="utf-8")
    saved = fetch_databento(
        start="2019-05-06",
        end="2019-05-08",
        out=directory,
        schema="tbbo",
        sessions_only="morning",
        client=client,
        say=lines.append,
    )
    assert settings.read_text(encoding="utf-8") == before
    assert saved == 2
    assert "records" in _kinds(client)
    assert KEY not in " ".join(lines)
    with gzip.open(directory / "2019-05-06.csv.gz", "rt", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["ts_event"].startswith("2019-05-06T09:00:00")
    assert row["ts_event"].endswith("-05:00")
    assert float(row["price"]) == pytest.approx(5000.25)
    assert row["size"] == "4"
    assert row["side"] == "B"
    assert float(row["bid_px"]) == pytest.approx(5000.0)
    assert float(row["ask_px"]) == pytest.approx(5000.25)
    assert row["bid_sz"] == "12"
    assert row["ask_sz"] == "9"
    assert row["contract"] == "MESM9:5"
    assert "action" not in row

    again = FakeDatabento(cost=99, rows=[_trade(monday)])
    spoken = []
    assert (
        fetch_databento(
            start="2019-05-06",
            end="2019-05-08",
            out=directory,
            schema="tbbo",
            sessions_only="morning",
            client=again,
            say=spoken.append,
        )
        == 2
    )
    assert "cost" not in _kinds(again)
    assert "records" not in _kinds(again)
    assert "already cover" in " ".join(spoken)

    full = FakeDatabento(cost=3, rows=[_trade(monday), _trade(tuesday, side="Bid")])
    fetch_databento(
        start="2019-05-06T05:00:00+00:00",
        end="2019-05-08T05:00:00+00:00",
        out=tmp_path / "allday",
        schema="trades",
        client=full,
        say=lines.append,
    )
    costs = [call for call in full.calls if isinstance(call, tuple) and call[0] == "cost"]
    assert len(costs) == 1
    assert costs[0][4] == "trades"
    with gzip.open(tmp_path / "allday" / "2019-05-07.csv.gz", "rt", encoding="utf-8", newline="") as handle:
        traded = next(csv.DictReader(handle))
    assert traded["side"] == "B"
    assert "bid_px" not in traded

    book = FakeDatabento(cost=1, rows=[_trade(monday, side="Ask")])
    fetch_databento(
        start="2019-05-06",
        end="2019-05-07",
        out=tmp_path / "book",
        schema="mbp-1",
        sessions_only="rth",
        client=book,
        say=lines.append,
    )
    with gzip.open(tmp_path / "book" / "2019-05-06.csv.gz", "rt", encoding="utf-8", newline="") as handle:
        updated = next(csv.DictReader(handle))
    assert updated["action"] == "T"
    assert updated["side"] == "A"
    assert float(updated["bid_px"]) == pytest.approx(5000.0)


def test_parquet_day_file_is_used_when_pyarrow_is_present(monkeypatch, tmp_path):
    from topstepbot.databentofetch import write_tick_day

    monkeypatch.setattr("topstepbot.databentofetch._parquet_available", lambda: True)
    captured = {}

    def fake_parquet(path, rows, schema):
        captured["rows"] = rows
        captured["schema"] = schema
        path.write_bytes(b"PAR1")

    monkeypatch.setattr("topstepbot.databentofetch._write_parquet", fake_parquet)
    client = FakeDatabento(cost=1, rows=[_trade(datetime(2019, 5, 6, 14, tzinfo=UTC))])
    fetch_databento(
        start="2019-05-06",
        end="2019-05-07",
        out=tmp_path / "flow",
        schema="tbbo",
        sessions_only="morning",
        client=client,
        say=lambda _line: None,
    )
    assert (tmp_path / "flow" / "2019-05-06.parquet").read_bytes() == b"PAR1"
    assert captured["schema"] == "tbbo"
    assert captured["rows"][0]["side"] == "B"
    assert "bid_px" in captured["rows"][0]
    written = write_tick_day(tmp_path / "direct", datetime(2019, 5, 6).date(), captured["rows"], "tbbo")
    assert written.name == "2019-05-06.parquet"


def test_a_weekend_request_and_a_bad_schema_download_nothing(tmp_path):
    lines = []
    with pytest.raises(SystemExit, match="No weekday session"):
        fetch_databento(
            start="2019-05-04",
            end="2019-05-06",
            out=tmp_path / "none",
            schema="tbbo",
            sessions_only="morning",
            client=FakeDatabento(),
            say=lines.append,
        )
    with pytest.raises(SystemExit, match="Schema"):
        fetch_databento(
            start="2019-05-06",
            schema="mbo",
            out=tmp_path / "no",
            client=FakeDatabento(),
            say=lines.append,
        )
    assert KEY not in " ".join(lines)


def test_rate_limit_on_the_cost_quote_retries_without_printing_the_key(tmp_path):
    class Flaky:
        def __init__(self):
            self.tries = 0
            self.api_key = KEY

        def dataset_bounds(self, schema="ohlcv-1m"):
            return (None, None)

        def cost(self, start, end, symbol, schema="ohlcv-1m"):
            self.tries += 1
            if self.tries == 1:
                raise RuntimeError(f"HTTP 429 for {self.api_key}")
            return 1.5

        def records(self, start, end, symbol, schema="ohlcv-1m"):
            raise AssertionError("downloaded")

    delays = []
    lines = []
    fetch_databento(
        start="2019-05-06",
        end="2019-05-07",
        out=tmp_path / "flow",
        schema="tbbo",
        quote_only=True,
        sessions_only="morning",
        client=Flaky(),
        say=lines.append,
        sleep=delays.append,
    )
    text = " ".join(lines)
    assert delays == [2.0]
    assert "1.50 US dollars" in text
    assert KEY not in text
    assert "[REDACTED]" in text


def test_mnq_named_roll_resets_the_opening_range(tmp_path):
    from zoneinfo import ZoneInfo

    ct = ZoneInfo("America/Chicago")
    engine = _engine(tmp_path)
    morning = datetime(2026, 1, 5, 8, 30, tzinfo=ct)
    engine.on_minute(_bar(morning, 18000, "MNQH4:1", high=18010, low=17990))
    engine.on_minute(_bar(morning.replace(hour=8, minute=44), 18005, "MNQH4:1", high=18011, low=17990))
    engine.on_minute(_bar(morning.replace(hour=8, minute=45), 18005, "MNQH4:1", high=18011, low=17990))
    assert engine.opening is not None
    assert engine.opening.high == 18011
    engine.broker.place_brackets(
        [
            BracketLeg(
                tag="t",
                side=Side.LONG,
                qty=1,
                entry_type="market",
                entry_price=None,
                stop_price=17000,
                target_price=19000,
                setup="A",
            )
        ]
    )
    engine.on_minute(_bar(morning.replace(hour=8, minute=46), 18000.5, "MNQH4:1", high=18001, low=17999))
    assert engine.broker.net_qty() == 1
    engine.on_minute(_bar(morning.replace(hour=9), 19000, "MNQM4:2", high=19010, low=18990))
    assert engine.broker.net_qty() == 0
    assert engine.opening is None
    rolled = [fill for fill in engine.broker._fills if fill.role == "contract roll"]
    assert rolled
    assert rolled[0].price < 18100


def test_history_report_opening_range_does_not_span_an_mnq_roll(tmp_path):
    from topstepbot.historyreport import write_history_report

    path = tmp_path / "mnq_roll.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume,contract\n"
        "2026-01-05T08:30:00-06:00,18000,18010,18000,18005,10,MNQH4:1\n"
        "2026-01-05T08:40:00-06:00,19000,19010,19000,19005,10,MNQM4:2\n"
        "2026-01-05T08:46:00-06:00,19005,19012,19002,19008,10,MNQM4:2\n",
        encoding="utf-8",
    )
    settings = Path("config/settings.yaml")
    before = settings.read_text(encoding="utf-8")
    config = load_config(settings)
    text = write_history_report(config, path, out=tmp_path / "report.txt", log_dir=tmp_path / "logs")
    assert settings.read_text(encoding="utf-8") == before
    assert "40.0 ticks" in text
    assert "4040" not in text
    assert "front month" in text
    assert "MNQM4:2" in text
    assert "not back-adjusted" in text


def test_source_does_not_print_the_key_or_place_orders():
    text = Path("topstepbot/databentofetch.py").read_text(encoding="utf-8")
    assert "print(api_key" not in text
    assert "print(key" not in text
    assert "/api/Order" not in text
    assert "metadata.get_cost" in text
    assert "MES.v.0" in text
    settings = Path("config/settings.yaml").read_text(encoding="utf-8")
    assert "retest_tolerance_ticks: 2" in settings or "retest_tolerance_ticks: 2" in settings.replace(" ", "")


def test_contract_roll_resets_the_opening_range_and_flattens_at_the_old_price(tmp_path):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    ct = ZoneInfo("America/Chicago")
    engine = _engine(tmp_path)
    morning = datetime(2026, 1, 5, 8, 30, tzinfo=ct)
    engine.on_minute(_bar(morning, 100, "MESU4:1", high=110, low=100))
    engine.on_minute(_bar(morning.replace(hour=8, minute=44), 105, "MESU4:1", high=111, low=100))
    engine.on_minute(_bar(morning.replace(hour=8, minute=45), 105, "MESU4:1", high=111, low=100))
    assert engine.opening is not None
    assert engine.opening.high == 111

    engine.broker.place_brackets(
        [
            BracketLeg(
                tag="t",
                side=Side.LONG,
                qty=1,
                entry_type="market",
                entry_price=None,
                stop_price=50,
                target_price=200,
                setup="A",
            )
        ]
    )
    engine.on_minute(_bar(morning.replace(hour=8, minute=46), 100.5, "MESU4:1", high=101, low=99.5))
    assert engine.broker.net_qty() == 1
    engine.on_minute(_bar(morning.replace(hour=9), 5000, "MESZ4:2", high=5010, low=4990))
    assert engine.broker.net_qty() == 0
    assert engine.opening is None
    rolled = [fill for fill in engine.broker._fills if fill.role == "contract roll"]
    assert rolled
    assert rolled[0].price < 200
    assert all(fill.price < 1000 for fill in engine.broker._fills)
    assert abs(engine.broker.group_realized("paper-1")) < 20
    assert all(slot[0] > 1000 for slot in engine._rth_stats.values())

    fresh = _engine(tmp_path)
    for minute in range(30, 46):
        fresh.on_minute(_bar(morning.replace(minute=minute), 100 + minute / 100, "", high=110, low=100))
    other = _engine(tmp_path)
    for minute in range(30, 46):
        other.on_minute(
            Bar(
                time=morning.replace(minute=minute),
                open=100 + minute / 100,
                high=110,
                low=100,
                close=100 + minute / 100,
                volume=10,
            )
        )
    assert [bar.close for bar in fresh.bars_5] == [bar.close for bar in other.bars_5]
    assert fresh._ema == other._ema

    rolled_engine = _engine(tmp_path)
    for offset in range(0, 6):
        rolled_engine.on_minute(_bar(morning + timedelta(minutes=offset), 100, "MESU4:1", high=110, low=90))
    for offset in range(6, 12):
        rolled_engine.on_minute(_bar(morning + timedelta(minutes=offset), 5000, "MESZ4:2", high=5010, low=4990))
    assert rolled_engine.bars_5
    assert all(bar.low > 1000 for bar in rolled_engine.bars_5)


def test_history_report_does_not_let_an_opening_range_span_a_roll(tmp_path):
    from topstepbot.historyreport import write_history_report

    path = tmp_path / "roll.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume,contract\n"
        "2026-01-05T08:30:00-06:00,100,110,100,105,10,MESU4:1\n"
        "2026-01-05T08:40:00-06:00,5000,5010,5000,5005,10,MESZ4:2\n"
        "2026-01-05T08:46:00-06:00,5005,5012,5002,5008,10,MESZ4:2\n",
        encoding="utf-8",
    )
    settings = Path("config/settings.yaml")
    before = settings.read_text(encoding="utf-8")
    config = load_config(settings)
    text = write_history_report(config, path, out=tmp_path / "report.txt", log_dir=tmp_path / "logs")
    assert settings.read_text(encoding="utf-8") == before
    assert "40.0 ticks" in text
    assert "19640" not in text
    assert "not back-adjusted" in text
    assert config.exits.retest_tolerance_ticks == 2

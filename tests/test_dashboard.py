"""Command center: plain sentences, a state file, and a localhost kill button."""

import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from topstepbot.board import (
    SessionPublisher,
    build_state,
    console_text,
    is_stale,
    quote_price,
    write_json,
)
from topstepbot.config import load_config
from topstepbot.dashboard import HOST, bind_server
from topstepbot.journal import Journal
from topstepbot.models import Bar, DayStats, OpeningRange, Side
from topstepbot.strategy.engine import PendingBreak
from topstepbot.timeutil import CHICAGO

CONFIG = load_config("config/settings.yaml")
NOW = datetime(2026, 10, 8, 9, 12, tzinfo=CHICAGO)


def _publisher(tmp_path) -> SessionPublisher:
    config = replace(
        CONFIG,
        runtime=replace(
            CONFIG.runtime,
            log_dir=str(tmp_path),
            kill_switch_file=str(tmp_path / "KILL"),
        ),
    )
    publisher = SessionPublisher(config)
    publisher.account_name = "50KTC-SKU-V2-DLL-694099-17865243"
    publisher.contract_name = "MESZ6"
    publisher.balance = 50000.0
    publisher.mode = "live"
    return publisher


def _engine(**overrides):
    opening = OpeningRange(NOW.date(), high=7832.25, low=7818.50)
    engine = SimpleNamespace(
        config=CONFIG,
        armed=True,
        day=DayStats(),
        day_flattened=False,
        opening=opening,
        pending=[],
        trade=None,
        minutes=[
            Bar(NOW, 7828, 7831, 7826, 7830, 100),
            Bar(NOW + timedelta(minutes=1), 7830, 7833, 7829, 7831, 80),
        ],
        session_day=NOW.date(),
        mll_floor=48000.0,
        cumulative_prior=0.0,
    )
    for key, value in overrides.items():
        setattr(engine, key, value)
    return engine


class _Broker:
    def __init__(self) -> None:
        self.last_price = 7830.0
        self._groups: dict = {}
        self._qty = 0
        self._avg = None

    def net_qty(self) -> int:
        return self._qty

    def average_price(self):
        return self._avg

    def unrealized(self, price: float) -> float:
        if self._qty == 0 or self._avg is None:
            return 0.0
        return (price - self._avg) * 5.0 * self._qty


def test_break_and_fakeout_become_plain_sentences():
    waiting = console_text(
        "SIGNAL setup=A side=long level=or_high price=7832.25 state=break_waiting_retest bar=2026-10-08T14:12:00+00:00"
    )
    assert waiting == "Price broke above 7832.25 — waiting for a pullback to retest."
    assert "break_waiting_retest" not in waiting
    short = console_text("SIGNAL setup=A side=short level=or_low price=7818.50 state=break_waiting_retest")
    assert short == "Price broke below 7818.50 — waiting for a bounce to retest."
    assert console_text("SKIP reason=fakeout A or_high long bar=2026-10-08T14:15:00+00:00") == "Skipped: fakeout."


def test_terminal_is_plain_and_the_log_file_keeps_the_detail(tmp_path, capsys):
    journal = Journal(tmp_path, clock=NOW)
    journal.signal(
        setup="A",
        side="long",
        level="or_high",
        price="7832.25",
        state="break_waiting_retest",
        bar="2026-10-08T14:12:00+00:00",
    )
    shown = capsys.readouterr().err
    assert "7832.25" in shown
    assert "pullback" in shown
    assert "break_waiting_retest" not in shown
    saved = journal.path.read_text(encoding="utf-8")
    assert "state=break_waiting_retest" in saved
    assert "SIGNAL" in saved


def test_watching_snapshot_and_event_file(tmp_path):
    publisher = _publisher(tmp_path)
    publisher.note_quote(7830.0, NOW)
    engine = _engine(pending=[PendingBreak("A", Side.LONG, "or_high", 7832.25, 1)])
    state = build_state(publisher, engine, _Broker(), NOW)
    assert state["status"] == "WATCHING"
    assert state["headline"] == "Price broke above 7832.25 — waiting for a pullback to retest."
    assert state["account_name"].startswith("50KTC")
    assert state["contract_name"] == "MESZ6"
    assert state["opening_range"] == {"high": 7832.25, "low": 7818.50}
    assert state["price"] == 7830.0
    assert state["price_source"] == "quote"
    assert "inside the opening range" in state["price_relation"]
    assert state["position"] is None
    assert state["bot_stop"] == 400
    assert state["bot_stop_remaining"] == 400
    assert state["topstep_daily_loss"] == 1000
    assert state["topstep_daily_remaining"] == 1000
    assert state["topstep_max_loss"] == 2000
    assert state["countdown_label"] == "Flat at 10:30 CT in"
    assert state["bars"]
    publisher.record(
        "info",
        "SIGNAL setup=A side=long level=or_high price=7832.25 state=break_waiting_retest bar=2026-10-08T14:12:00+00:00",
    )
    events = json.loads(publisher.events_path.read_text(encoding="utf-8"))
    assert events[-1]["text"] == "Price broke above 7832.25 — waiting for a pullback to retest."
    assert "break_waiting_retest" not in events[-1]["text"]
    write_json(publisher.state_path, state, publisher.secrets)
    assert not publisher.state_path.with_suffix(".json.tmp").exists()
    assert json.loads(publisher.state_path.read_text(encoding="utf-8"))["status"] == "WATCHING"


def test_in_trade_snapshot_shows_dollars(tmp_path):
    publisher = _publisher(tmp_path)
    publisher.note_quote(7836.0, NOW)
    trade = SimpleNamespace(
        side=Side.LONG,
        filled_qty=2,
        entry_notional=7832.50 * 2,
        initial_stop=7828.0,
        groups=["g"],
    )
    broker = _Broker()
    broker._qty = 2
    broker._avg = 7832.50
    broker._groups = {"g": {"stop_price": 7828.0, "target_price": 7842.0}}
    state = build_state(publisher, _engine(trade=trade, pending=[]), broker, NOW)
    assert state["status"] == "IN TRADE"
    assert state["position"]["side"] == "Long"
    assert state["position"]["contracts"] == 2
    assert state["position"]["entry"] == 7832.5
    assert state["position"]["stop"] == 7828.0
    assert state["position"]["target"] == 7842.0
    assert state["position"]["unrealized"] == 35.0
    assert state["pnl"] == 35.0
    assert "Long 2" in state["headline"]


def test_halt_flat_kill_and_stale_quote(tmp_path):
    publisher = _publisher(tmp_path)
    broker = _Broker()
    halted = _engine()
    halted.day.halted = True
    halted.day.halt_reason = "daily max loss"
    assert build_state(publisher, halted, broker, NOW)["status"] == "HALTED"
    flat = _engine(day_flattened=True)
    flat.day.halt_reason = "session flatten"
    assert build_state(publisher, flat, broker, NOW)["status"] == "FLAT"
    publisher.mode = "killed"
    assert build_state(publisher, _engine(), broker, NOW)["status"] == "KILLED"
    publisher.mode = "live"
    publisher.note_quote(7830.0, NOW - timedelta(seconds=45))
    assert build_state(publisher, _engine(), broker, NOW)["status"] == "DISCONNECTED"
    early = datetime(2026, 10, 8, 8, 0, tzinfo=CHICAGO)
    before = build_state(publisher, None, None, early)
    assert before["countdown_label"] == "Session starts in"
    fresh = {"updated_at": NOW.isoformat()}
    assert is_stale(fresh, NOW) is False
    assert is_stale(fresh, NOW + timedelta(seconds=46)) is True
    assert is_stale(None, NOW) is True


def test_warmup_events_are_not_recorded_and_secrets_are_removed(tmp_path):
    publisher = _publisher(tmp_path)
    publisher.secrets = ["super-secret-key-value"]
    publisher.mode = "warming"
    publisher.record("info", "SIGNAL setup=A side=long price=1 state=break_waiting_retest")
    assert not publisher.events_path.exists()
    publisher.mode = "live"
    publisher.record("error", "login failed super-secret-key-value")
    text = publisher.events_path.read_text(encoding="utf-8")
    assert "super-secret-key-value" not in text
    assert "[REDACTED]" in text
    write_json(tmp_path / "state.json", {"apiKey": "super-secret-key-value", "ok": True}, publisher.secrets)
    saved = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert "super-secret-key-value" not in saved


def test_quote_price_reads_last_price():
    assert quote_price(("CON.F.US.MES.Z26", {"lastPrice": 7834.25, "bestBid": 7834.0, "bestAsk": 7834.5})) == 7834.25
    assert quote_price(({"bestBid": 10, "bestAsk": 12},)) == 11


def test_dashboard_binds_localhost_and_kill_needs_confirm(tmp_path):
    try:
        bind_server("0.0.0.0", 0, tmp_path, tmp_path / "KILL")
        raise AssertionError("expected a refusal")
    except SystemExit as exc:
        assert "127.0.0.1" in str(exc)
    server = bind_server(HOST, 0, tmp_path, tmp_path / "KILL")
    assert server.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        page = urlopen(base + "/", timeout=2).read().decode("utf-8")
        assert "KILL" in page
        assert "0.0.0.0" not in page
        # The page redraws every second. The kill sentence has to live outside that redraw.
        assert "let killNote" in page
        assert "showKillNote()" in page
        assert "Kill sent. The bot will flatten and stop." in page
        missing = json.loads(urlopen(base + "/api/state", timeout=2).read().decode("utf-8"))
        assert missing["stale"] is True
        assert missing["state"] is None
        try:
            urlopen(
                Request(base + "/api/kill", data=b"{}", headers={"Content-Type": "application/json"}, method="POST"),
                timeout=2,
            )
            raise AssertionError("kill without confirm must be refused")
        except HTTPError as exc:
            assert exc.code == 400
        assert not (tmp_path / "KILL").exists()
        accepted = urlopen(
            Request(
                base + "/api/kill",
                data=json.dumps({"confirm": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=2,
        )
        assert accepted.status == 200
        assert "dashboard kill" in (tmp_path / "KILL").read_text(encoding="utf-8")
        write_json(tmp_path / "state.json", {"updated_at": datetime.now(CHICAGO).isoformat(), "status": "ARMED"})
        fresh = json.loads(urlopen(base + "/api/state", timeout=2).read().decode("utf-8"))
        assert fresh["stale"] is False
        assert fresh["state"]["status"] == "ARMED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

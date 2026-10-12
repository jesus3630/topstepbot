"""The armed warmup used to call Trade/search once per historical minute."""

from datetime import datetime, timedelta, timezone

from topstepbot.broker.projectx import ApiHalted, ProjectXBroker, ProjectXClient, ProjectXError
from topstepbot.broker.ratelimit import ErrorFold
from topstepbot.config import load_config
from topstepbot.journal import Journal
from topstepbot.live import _poll_once, _reconcile_startup, _stop_for_api_failure
from topstepbot.models import AccountInfo, Bar, BracketLeg, Side
from topstepbot.strategy.engine import StrategyEngine
from topstepbot.timeutil import CHICAGO

CONFIG = load_config("config/settings.yaml")
CONTRACT = {
    "id": "CON.F.US.MES.Z26",
    "name": "MESZ6",
    "tickSize": 0.25,
    "tickValue": 1.25,
}


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}
        self._body = {"success": True, "errorCode": 0, "trades": []} if body is None else body

    def json(self):
        return self._body


class QueueSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.posts: list[str] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append(url)
        if not self.responses:
            raise AssertionError(f"unexpected post {url}")
        return self.responses.pop(0)


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.positions: list[dict] = []
        self.orders: list[dict] = []
        self.fail = False

    def search_trades(self, account_id, start, end=None):
        self.calls.append("/api/Trade/search")
        if self.fail:
            raise ProjectXError("ProjectX rate limit (HTTP 429). Back off and try again.")
        return []

    def search_open_positions(self, account_id):
        self.calls.append("/api/Position/searchOpen")
        if self.fail:
            raise ProjectXError("down")
        return list(self.positions)

    def search_open_orders(self, account_id):
        self.calls.append("/api/Order/searchOpen")
        if self.fail:
            raise ProjectXError("down")
        return list(self.orders)

    def close_contract(self, account_id, contract_id):
        self.calls.append("/api/Position/closeContract")

    def cancel_order(self, account_id, order_id):
        self.calls.append("/api/Order/cancel")

    def place_order(self, payload):
        self.calls.append("/api/Order/place")
        return 1

    def retrieve_bars(self, *args, **kwargs):
        self.calls.append("/api/History/retrieveBars")
        return list(kwargs.get("bars") or self._bars)

    def search_contracts(self, *args, **kwargs):
        self.calls.append("/api/Contract/search")
        raise AssertionError("contract search is not allowed inside the live loop")

    def search_accounts(self, *args, **kwargs):
        self.calls.append("/api/Account/search")
        raise AssertionError("account search is not allowed inside the live loop")


def _account() -> AccountInfo:
    return AccountInfo(id=28359182, name="50KTC-TEST", can_trade=True, simulated=True)


def _broker(client, tmp_path) -> tuple[ProjectXBroker, Journal]:
    journal = Journal(tmp_path)
    broker = ProjectXBroker(CONFIG, client, _account(), CONTRACT)
    return broker, journal


def _bars_from(moment: datetime, count: int) -> list[Bar]:
    bars = []
    for index in range(count):
        when = moment + timedelta(minutes=index)
        bars.append(Bar(time=when, open=5000, high=5001, low=4999, close=5000.25, volume=100))
    return bars


def _client(session: QueueSession, clock: ManualClock, **kwargs) -> ProjectXClient:
    client = ProjectXClient(
        "trader",
        "key",
        session=session,
        clock=clock.clock,
        sleep=clock.sleep,
        jitter=kwargs.pop("jitter", lambda: 0.0),
        **kwargs,
    )
    client.token = "session-token"
    client.token_acquired_at = datetime.now(timezone.utc)
    return client


def test_warmup_replay_does_not_search_or_place(tmp_path):
    client = RecordingClient()
    broker, journal = _broker(client, tmp_path)
    broker.orders_enabled = False
    start = datetime(2026, 10, 8, 8, 30, tzinfo=CHICAGO)
    bars = _bars_from(start, 121)
    engine = StrategyEngine(CONFIG, broker, journal, armed=False)
    for bar in bars:
        engine.on_minute(bar)
        broker.on_bar(bar)
    assert engine.armed is False
    assert client.calls == []
    broker.orders_enabled = True
    for bar in bars[:10]:
        assert broker.on_bar(bar) == []
    assert client.calls == []
    assert broker.poll(bars[-1].time) == []
    assert client.calls == ["/api/Trade/search"]


def test_live_cycle_searches_trades_once_for_many_bars(tmp_path):
    client = RecordingClient()
    start = datetime(2026, 10, 8, 9, 0, tzinfo=CHICAGO)
    client._bars = _bars_from(start, 3)
    broker, journal = _broker(client, tmp_path)
    engine = StrategyEngine(CONFIG, broker, journal, armed=False)
    for bar in client._bars:
        engine.on_minute(bar)
    client.calls.clear()
    now = start + timedelta(minutes=4)
    fresh = _bars_from(start + timedelta(minutes=3), 3)
    client._bars = fresh
    action = _poll_once(client, broker, engine, CONTRACT["id"], CONFIG, {bar.time for bar in []}, now, journal)
    assert action == "continue"
    assert client.calls == ["/api/History/retrieveBars", "/api/Trade/search"]


def test_retry_after_is_honored_then_the_read_succeeds():
    clock = ManualClock()
    session = QueueSession(
        [
            FakeResponse(429, headers={"Retry-After": "5"}),
            FakeResponse(200, {"success": True, "errorCode": 0, "trades": []}),
        ]
    )
    client = _client(session, clock, jitter=lambda: 0.25, max_consecutive_failures=5)
    assert client.search_trades(1, datetime(2026, 10, 8, tzinfo=timezone.utc)) == []
    assert session.posts and session.posts[0].endswith("/api/Trade/search")
    assert len(session.posts) == 2
    assert clock.sleeps == [5.25]
    assert client.limiter.halted is False


def test_five_429s_halt_and_the_next_call_does_not_hit_http():
    clock = ManualClock()
    session = QueueSession([FakeResponse(429) for _ in range(8)])
    client = _client(session, clock, max_consecutive_failures=5, failure_window_seconds=30)
    try:
        client.search_trades(1, datetime(2026, 10, 8, tzinfo=timezone.utc))
        raise AssertionError("expected a halt")
    except ApiHalted as exc:
        assert "Trading stopped" in str(exc)
    assert len(session.posts) == 5
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0]
    try:
        client.search_trades(1, datetime(2026, 10, 8, tzinfo=timezone.utc))
        raise AssertionError("halted client must not call HTTP")
    except ApiHalted:
        pass
    assert len(session.posts) == 5


def test_failure_window_halts_before_the_consecutive_cap():
    clock = ManualClock()
    session = QueueSession([FakeResponse(429, headers={"Retry-After": "6"}) for _ in range(6)])
    client = _client(session, clock, max_consecutive_failures=100, failure_window_seconds=10)
    try:
        client.search_accounts(True)
        raise AssertionError("expected a halt")
    except ApiHalted:
        pass
    assert len(session.posts) == 3
    assert clock.sleeps == [6.0, 6.0]


def test_other_bucket_waits_instead_of_bursting():
    clock = ManualClock()
    session = QueueSession(
        [FakeResponse(200, {"success": True, "errorCode": 0, "accounts": []}) for _ in range(3)]
    )
    client = _client(session, clock, other_limit=(2, 10.0), history_limit=(50, 30.0))
    client.search_accounts(True)
    client.search_accounts(True)
    client.search_accounts(True)
    assert len(session.posts) == 3
    assert clock.sleeps == [10.0]


def test_history_bucket_does_not_block_trade_search():
    clock = ManualClock()
    bars = {"success": True, "errorCode": 0, "bars": []}
    trades = {"success": True, "errorCode": 0, "trades": []}
    session = QueueSession(
        [
            FakeResponse(200, bars),
            FakeResponse(200, trades),
            FakeResponse(200, bars),
        ]
    )
    client = _client(session, clock, history_limit=(1, 30.0), other_limit=(10, 60.0))
    end = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
    client.retrieve_bars("CON.F.US.MES.Z26", end - timedelta(minutes=5), end)
    client.search_trades(1, end - timedelta(minutes=5), end)
    client.retrieve_bars("CON.F.US.MES.Z26", end - timedelta(minutes=5), end)
    assert [url.rsplit("/", 1)[-1] for url in session.posts] == [
        "retrieveBars",
        "search",
        "retrieveBars",
    ]
    assert clock.sleeps == [30.0]


def test_order_place_is_not_retried_after_429():
    clock = ManualClock()
    session = QueueSession([FakeResponse(429, headers={"Retry-After": "5"}), FakeResponse(200)])
    client = _client(session, clock)
    try:
        client.place_order(
            {
                "accountId": 1,
                "contractId": "CON.F.US.MES.Z26",
                "type": 2,
                "side": 0,
                "size": 1,
                "stopLossBracket": {"ticks": 4, "type": 4},
            }
        )
        raise AssertionError("expected the place call to fail")
    except ProjectXError as exc:
        assert "not retried" in str(exc)
    assert len(session.posts) == 1
    assert session.posts[0].endswith("/api/Order/place")
    assert client.limiter.halted is False


def test_repeated_errors_log_once_with_a_counter():
    lines: list[str] = []
    fold = ErrorFold(lines.append)
    message = "Trade search failed: ProjectX rate limit (HTTP 429). Back off and try again."
    for _ in range(100):
        fold.report(message)
    fold.flush()
    assert lines == [message, f"{message} (repeated 99 more times)"]


def test_poll_folds_the_same_trade_search_error(tmp_path):
    client = RecordingClient()
    client.fail = True
    broker, _journal = _broker(client, tmp_path)
    lines: list[str] = []
    broker._errors = ErrorFold(lines.append)
    when = datetime(2026, 10, 8, 9, 0, tzinfo=CHICAGO)
    for _ in range(100):
        assert broker.poll(when) == []
    broker._errors.flush()
    assert len(lines) == 2
    assert lines[0].startswith("Trade search failed:")
    assert "repeated 99 more times" in lines[1]


def test_poll_does_not_swallow_a_halt(tmp_path):
    client = RecordingClient()

    def explode(*_args, **_kwargs):
        raise ApiHalted("ProjectX API halted after 5 consecutive failures (0s). Trading stopped.")

    client.search_trades = explode
    broker, _journal = _broker(client, tmp_path)
    try:
        broker.poll(datetime(2026, 10, 8, 9, 0, tzinfo=CHICAGO))
        raise AssertionError("halt must propagate")
    except ApiHalted:
        pass


def test_suspended_broker_refuses_to_place(tmp_path):
    client = RecordingClient()
    broker, _journal = _broker(client, tmp_path)
    broker.orders_enabled = False
    leg = BracketLeg(
        tag="A-long-t1-1",
        side=Side.LONG,
        qty=1,
        entry_type="market",
        entry_price=5000,
        stop_price=4998,
        target_price=5004,
        setup="A",
    )
    try:
        broker.place_brackets([leg])
        raise AssertionError("expected a refusal")
    except ProjectXError:
        pass
    assert client.calls == []


def test_stop_for_api_failure_reports_the_book_once(tmp_path):
    client = RecordingClient()
    client.positions = [{"contractId": CONTRACT["id"], "size": 1, "averagePrice": 5000}]
    client.orders = [{"id": 9, "contractId": CONTRACT["id"], "type": 4, "side": 1, "size": 1}]
    broker, journal = _broker(client, tmp_path)
    # Journal also writes the message. Capture it through a stand-in as well.
    notes = _Notes()
    _stop_for_api_failure(broker, notes, ApiHalted("ProjectX API halted after 5 consecutive failures (4s). Trading stopped."))
    assert broker.orders_enabled is False
    assert [line for line in notes.errors if "Trading stopped" in line] == notes.errors[:1]
    assert len([line for line in notes.errors if "Trading stopped" in line]) == 1
    assert any("Open position" in line and "size=1" in line for line in notes.infos)
    assert any("Working order" in line and "id=9" in line for line in notes.infos)
    assert client.calls == ["/api/Position/searchOpen", "/api/Order/searchOpen"]


def test_stop_for_api_failure_does_not_retry_a_failed_book_check(tmp_path):
    client = RecordingClient()
    client.fail = True
    broker, _journal = _broker(client, tmp_path)
    notes = _Notes()
    _stop_for_api_failure(broker, notes, ProjectXError("ProjectX rate limit (HTTP 429). Back off and try again."))
    assert broker.orders_enabled is False
    assert len([line for line in notes.errors if "Trading stopped" in line]) == 1
    assert len([line for line in notes.errors if "Could not list open positions" in line]) == 1
    assert len([line for line in notes.errors if "Could not list open orders" in line]) == 1
    assert client.calls == ["/api/Position/searchOpen", "/api/Order/searchOpen"]


def test_startup_reconcile_prints_a_flat_book_and_does_not_flatten(tmp_path):
    client = RecordingClient()
    broker, journal = _broker(client, tmp_path)
    notes = _Notes()
    _reconcile_startup(broker, notes, flatten_on_start=True)
    assert notes.infos == ["No open positions.", "No working orders."]
    assert client.calls == ["/api/Position/searchOpen", "/api/Order/searchOpen"]


def test_startup_reconcile_flattens_an_open_position_once(tmp_path):
    client = RecordingClient()
    client.positions = [{"contractId": CONTRACT["id"], "size": 2, "averagePrice": 5100}]
    client.orders = [{"id": 4, "contractId": CONTRACT["id"], "type": 4, "side": 1, "size": 2}]
    broker, _journal = _broker(client, tmp_path)
    notes = _Notes()
    _reconcile_startup(broker, notes, flatten_on_start=True)
    assert any("Open position" in line for line in notes.infos)
    assert any("Flattening once" in line for line in notes.infos)
    assert client.calls.count("/api/Position/closeContract") == 1
    assert client.calls.count("/api/Order/cancel") == 1
    assert client.calls.count("/api/Trade/search") == 1


def test_halted_probe_is_single_shot():
    clock = ManualClock()
    session = QueueSession([FakeResponse(429) for _ in range(6)] + [FakeResponse(429), FakeResponse(429)])
    client = _client(session, clock, max_consecutive_failures=5)
    try:
        client.search_open_positions(1)
    except ApiHalted:
        pass
    assert len(session.posts) == 5
    client.allow_halt_probe(2)
    try:
        client.search_open_positions(1)
        raise AssertionError("probe 429 must fail without a retry loop")
    except ProjectXError as exc:
        assert not isinstance(exc, ApiHalted)
    try:
        client.search_open_orders(1)
        raise AssertionError("second probe must also be single-shot")
    except ProjectXError:
        pass
    assert len(session.posts) == 7
    try:
        client.search_open_positions(1)
        raise AssertionError("probe budget is spent")
    except ApiHalted:
        pass
    assert len(session.posts) == 7


class _Notes:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.infos: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def info(self, message: str) -> None:
        self.infos.append(message)

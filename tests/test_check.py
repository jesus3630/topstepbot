"""Read-only connection check. HTTP is mocked. No socket is opened."""

import inspect
import json
from datetime import datetime, timezone

from topstepbot.broker.readonly import READ_ONLY_PATHS, ReadOnlyProjectXClient, ReadOnlyViolation
from topstepbot.check import classify_bar_order, identify_50k_combines, listen_market_quotes, run_check
from topstepbot.cli import main
from topstepbot.config import load_config
from topstepbot.models import AccountInfo

CONFIG = load_config("config/settings.yaml")
API_KEY = "super-secret-key"
TOKEN = "super-secret-token"
USERNAME = "trader.one"

CONTRACTS = {
    "success": True,
    "errorCode": 0,
    "hasBusinessFailure": False,
    "contracts": [
        {
            "id": "CON.F.US.ENQ.Z26",
            "name": "NQZ26",
            "description": "E-mini NASDAQ",
            "tickSize": 0.25,
            "tickValue": 5,
            "activeContract": True,
            "symbolId": "F.US.ENQ",
        },
        {
            "id": "CON.F.US.MES.Z26",
            "name": "MESZ26",
            "description": "Micro E-mini S&P 500",
            "tickSize": 0.25,
            "tickValue": 1.25,
            "activeContract": True,
            "symbolId": "F.US.MES",
        },
        {
            "id": "CON.F.US.MES.H27",
            "name": "MESH27",
            "description": "Micro E-mini S&P 500",
            "tickSize": 0.25,
            "tickValue": 1.25,
            "activeContract": False,
            "symbolId": "F.US.MES",
        },
    ],
}

NEWEST_FIRST_BARS = {
    "success": True,
    "errorCode": 0,
    "hasBusinessFailure": False,
    "bars": [
        {"t": "2026-10-07T14:05:00Z", "o": 5801, "h": 5802, "l": 5800, "c": 5801.25, "v": 40},
        {"t": "2026-10-07T14:00:00Z", "o": 5800, "h": 5801, "l": 5799, "c": 5800.5, "v": 30},
        {"t": "2026-10-07T13:55:00Z", "o": 5799, "h": 5800, "l": 5798, "c": 5799.75, "v": 20},
    ],
}


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        for suffix, body in self.routes.items():
            if url.endswith(suffix):
                return FakeResponse(200, body)
        return FakeResponse(404, {"success": False, "errorCode": 1, "errorMessage": "not in the fake"})


def _routes(accounts, bars=None, login_code=0):
    if login_code == 0:
        login = {
            "success": True,
            "errorCode": 0,
            "hasBusinessFailure": False,
            "token": TOKEN,
        }
    else:
        login = {
            "success": False,
            "errorCode": login_code,
            "hasBusinessFailure": True,
            "errorMessage": "Invalid credentials",
            "token": None,
        }
    return {
        "/api/Auth/loginKey": login,
        "/api/Account/search": accounts,
        "/api/Contract/search": CONTRACTS,
        "/api/History/retrieveBars": bars or NEWEST_FIRST_BARS,
    }


def _accounts(*rows):
    return {
        "success": True,
        "errorCode": 0,
        "hasBusinessFailure": False,
        "accounts": list(rows),
    }


def _combine_row():
    return {
        "id": 22,
        "name": "50KTC-V2-999",
        "balance": 50000,
        "canTrade": True,
        "isVisible": True,
        "simulated": True,
    }


def _practice_row():
    return {
        "id": 11,
        "name": "PRAC-V2-1",
        "balance": 150000,
        "canTrade": True,
        "isVisible": True,
        "simulated": False,
    }


def _run(tmp_path, session, **kwargs):
    import io

    buffer = io.StringIO()
    code = run_check(
        CONFIG,
        session=session,
        username=USERNAME,
        api_key=API_KEY,
        api_url="https://api.topstepx.com",
        market_hub_url=kwargs.pop("market_hub_url", "https://rtc.topstepx.com/hubs/market"),
        log_dir=tmp_path,
        now=datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc),
        stdout=buffer,
        skip_signalr=kwargs.pop("skip_signalr", True),
        **kwargs,
    )
    text = buffer.getvalue()
    report = json.loads((tmp_path / "check-20261007T150000000000Z.json").read_text(encoding="utf-8"))
    return code, text, report


def test_order_methods_are_disabled_and_never_touch_http():
    session = FakeSession(_routes(_accounts(_combine_row())))
    client = ReadOnlyProjectXClient("user", API_KEY, session=session)
    assert set(READ_ONLY_PATHS) == {
        "/api/Auth/loginKey",
        "/api/Account/search",
        "/api/Contract/search",
        "/api/History/retrieveBars",
    }
    assert all("/Order" not in path and "Position" not in path for path in READ_ONLY_PATHS)
    for name, args in (
        ("place_order", ({"accountId": 1},)),
        ("cancel_order", (1, 99)),
        ("modify_order", (1, 99)),
        ("search_open_orders", (1,)),
        ("close_contract", (1, "CON.F.US.MES.Z26")),
        ("partial_close", (1, "CON.F.US.MES.Z26", 1)),
        ("search_open_positions", (1,)),
        ("search_trades", (1,)),
    ):
        try:
            getattr(client, name)(*args)
            raise AssertionError(f"{name} should be disabled")
        except ReadOnlyViolation as exc:
            assert "disabled" in str(exc)
    for path in (
        "/api/Order/place",
        "/api/Order/cancel",
        "/api/Order/modify",
        "/api/Order/searchOpen",
        "/api/Position/closeContract",
        "/api/Position/partialCloseContract",
        "/api/Trade/search",
    ):
        try:
            client._post(path, {"accountId": 1})
            raise AssertionError(f"{path} should be refused")
        except ReadOnlyViolation:
            pass
    assert session.calls == []


def test_check_source_does_not_call_order_routes():
    import topstepbot.check as check_module

    source = inspect.getsource(check_module)
    assert "/api/Order" not in source
    assert "place_order" not in source
    assert "ProjectXBroker" not in source
    assert "ReadOnlyProjectXClient(" in source
    assert "import ProjectXClient" not in source


def test_check_prints_combine_contract_and_newest_first_bars(tmp_path):
    accounts = _accounts(_practice_row(), _combine_row())
    # Drop simulated on the combine so the report shows the field as absent.
    combine = dict(_combine_row())
    del combine["simulated"]
    accounts["accounts"][1] = combine
    session = FakeSession(_routes(accounts))
    code, text, report = _run(tmp_path, session)
    assert code == 0
    assert "PASS  Logged in" in text
    assert "id=22  name=50KTC-V2-999" in text
    assert "balance=50000" in text
    assert "canTrade=true" in text
    assert "simulated=field absent" in text
    assert "id=11  name=PRAC-V2-1" in text
    assert "simulated=false" in text
    assert "50K Combine: id=22 name=50KTC-V2-999" in text
    assert "id=CON.F.US.MES.Z26" in text
    assert "tickSize=0.25" in text
    assert "tickValue=1.25" in text
    assert "Front month the bot would pick: id=CON.F.US.MES.Z26 name=MESZ26" in text
    assert "count: 3" in text
    assert "first timestamp (as returned): 2026-10-07T14:05:00Z" in text
    assert "last timestamp (as returned): 2026-10-07T13:55:00Z" in text
    assert "arrival order: newest-first" in text
    assert "OVERALL PASS" in text
    summary = text.split("SUMMARY", 1)[1]
    for name, status in (
        ("auth", "PASS"),
        ("accounts", "PASS"),
        ("contracts", "PASS"),
        ("bars", "PASS"),
        ("signalr", "SKIP"),
    ):
        line = next(item for item in summary.splitlines() if item.strip().startswith(name))
        assert status in line
    assert API_KEY not in text
    assert TOKEN not in text
    raw = (tmp_path / "check-20261007T150000000000Z.json").read_text(encoding="utf-8")
    assert API_KEY not in raw
    assert TOKEN not in raw
    assert report["steps"]["auth"]["response"]["token"] == "[REDACTED]"
    assert report["steps"]["bars"]["arrival_order"] == "newest-first"
    assert report["read_only"] is True
    urls = [call["url"] for call in session.calls]
    assert urls == [
        "https://api.topstepx.com/api/Auth/loginKey",
        "https://api.topstepx.com/api/Account/search",
        "https://api.topstepx.com/api/Contract/search",
        "https://api.topstepx.com/api/History/retrieveBars",
    ]
    assert all("/api/Order" not in url for url in urls)
    bar_call = session.calls[-1]["json"]
    assert bar_call["contractId"] == "CON.F.US.MES.Z26"
    assert bar_call["unit"] == 2
    assert bar_call["unitNumber"] == 5
    assert bar_call["includePartialBar"] is False
    assert bar_call["live"] is False
    assert session.calls[0]["json"]["apiKey"] == API_KEY
    assert "Authorization" not in session.calls[0]["headers"]
    assert session.calls[1]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_login_failure_stops_before_any_other_call(tmp_path):
    session = FakeSession(_routes(_accounts(_combine_row()), login_code=3))
    code, text, report = _run(tmp_path, session)
    assert code == 1
    assert "FAIL" in text
    assert "errorCode 3" in text
    assert "OVERALL FAIL" in text
    assert API_KEY not in text
    assert report["steps"]["auth"]["status"] == "FAIL"
    assert report["steps"]["accounts"]["status"] == "SKIP"
    assert [call["url"] for call in session.calls] == ["https://api.topstepx.com/api/Auth/loginKey"]


def test_missing_credentials_do_not_call_http(tmp_path):
    session = FakeSession(_routes(_accounts(_combine_row())))
    import io

    buffer = io.StringIO()
    code = run_check(
        CONFIG,
        session=session,
        username="",
        api_key="",
        log_dir=tmp_path,
        now=datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc),
        stdout=buffer,
        skip_signalr=True,
    )
    text = buffer.getvalue()
    assert code == 1
    assert "Missing PROJECTX_USERNAME" in text
    assert session.calls == []


def test_oldest_first_bars_are_reported(tmp_path):
    bars = {
        "success": True,
        "errorCode": 0,
        "hasBusinessFailure": False,
        "bars": list(reversed(NEWEST_FIRST_BARS["bars"])),
    }
    session = FakeSession(_routes(_accounts(_combine_row()), bars=bars))
    code, text, report = _run(tmp_path, session)
    assert code == 0
    assert "arrival order: oldest-first" in text
    assert report["steps"]["bars"]["arrival_order"] == "oldest-first"
    assert "first timestamp (as returned): 2026-10-07T13:55:00Z" in text


def test_signalr_handshake_failure_is_redacted_and_fails_the_check(tmp_path):
    session = FakeSession(_routes(_accounts(_combine_row())))

    def explode(client, contract_id, seconds, sleep=None):
        raise ConnectionError(f"handshake failed access_token={client.token} key={API_KEY}")

    code, text, report = _run(
        tmp_path,
        session,
        skip_signalr=False,
        listen_quotes=explode,
        signalr_seconds=20,
        market_hub_url=f"https://rtc.topstepx.com/hubs/market?access_token={TOKEN}",
    )
    assert code == 1
    assert "OVERALL FAIL" in text
    assert "hub: wss://rtc.topstepx.com/hubs/market (token not shown)" in text
    assert "handshake failed" in text
    assert "signalr" in text and "FAIL" in text
    assert TOKEN not in text
    assert API_KEY not in text
    hub_line = next(line for line in text.splitlines() if line.strip().startswith("hub:"))
    assert hub_line.strip() == "hub: wss://rtc.topstepx.com/hubs/market (token not shown)"
    assert TOKEN not in json.dumps(report)
    assert API_KEY not in json.dumps(report)
    assert report["steps"]["signalr"]["status"] == "FAIL"
    assert report["steps"]["signalr"]["hub"] == "wss://rtc.topstepx.com/hubs/market"


def test_signalr_sample_is_printed_and_the_hub_is_not_required_to_fail_the_check(tmp_path):
    session = FakeSession(_routes(_accounts(_combine_row())))

    def quotes(client, contract_id, seconds, sleep=None):
        assert contract_id == "CON.F.US.MES.Z26"
        assert seconds == 20
        return {
            "ok": True,
            "quotes": [
                {
                    "contractId": contract_id,
                    "data": {"lastPrice": 5800.25, "bestBid": 5800.0, "bestAsk": 5800.5, "symbol": "MES"},
                }
            ],
        }

    code, text, report = _run(tmp_path, session, skip_signalr=False, listen_quotes=quotes, signalr_seconds=20)
    assert code == 0
    assert "quotes arrived: 1" in text
    assert "sample: contractId=CON.F.US.MES.Z26 lastPrice=5800.25 bestBid=5800.0 bestAsk=5800.5" in text
    assert "Disconnected." in text
    assert TOKEN not in text
    assert report["steps"]["signalr"]["status"] == "PASS"
    assert report["steps"]["signalr"]["quote_count"] == 1


def test_zero_quotes_fail_when_the_mes_session_is_open_and_warn_when_it_is_closed(tmp_path):
    session = FakeSession(_routes(_accounts(_combine_row())))

    def no_quotes(client, contract_id, seconds, sleep=None):
        return {"ok": True, "quotes": [], "public_url": "wss://rtc.topstepx.com/hubs/market"}

    code, text, report = _run(tmp_path, session, skip_signalr=False, listen_quotes=no_quotes, signalr_seconds=20)
    assert code == 1
    assert "0 quotes while the CME equity-index session is open" in text
    assert report["steps"]["signalr"]["status"] == "FAIL"
    assert report["steps"]["signalr"]["market_open"] is True
    assert "OVERALL FAIL" in text

    import io

    closed = io.StringIO()
    saturday = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
    closed_dir = tmp_path / "closed"
    code = run_check(
        CONFIG,
        session=FakeSession(_routes(_accounts(_combine_row()))),
        username=USERNAME,
        api_key=API_KEY,
        log_dir=closed_dir,
        now=saturday,
        stdout=closed,
        skip_signalr=False,
        listen_quotes=no_quotes,
        signalr_seconds=20,
    )
    closed_text = closed.getvalue()
    assert code == 0
    assert "WARN" in closed_text
    assert "session is closed" in closed_text
    assert "OVERALL PASS" in closed_text
    assert API_KEY not in closed_text


def test_signalr_listener_counts_one_quote_and_disconnects():
    session = FakeSession({})
    client = ReadOnlyProjectXClient("user", API_KEY, session=session)
    client.token = TOKEN

    class FakeHub:
        def __init__(self):
            self.handlers = {}
            self.sent = []
            self.stopped = False

        def on(self, name, callback):
            self.handlers[name] = callback

        def start(self):
            self.handlers["GatewayQuote"](
                "CON.F.US.MES.Z26",
                {"lastPrice": 5800.25, "bestBid": 5800.0, "bestAsk": 5800.5, "symbol": "MES"},
            )

        def send(self, method, args):
            self.sent.append((method, args))

        def stop(self):
            self.stopped = True

    hub = FakeHub()
    client._hub_factory = lambda _client: hub
    result = listen_market_quotes(client, "CON.F.US.MES.Z26", 20, sleep=lambda _seconds: None)
    assert result["ok"] is True
    assert hub.stopped is True
    assert hub.sent == [("SubscribeContractQuotes", ["CON.F.US.MES.Z26"])]
    assert result["quotes"][0]["data"]["lastPrice"] == 5800.25
    assert session.calls == []

    class FailingHub(FakeHub):
        def start(self):
            raise ConnectionError("websocket handshake failed")

    failed = FailingHub()
    client._hub_factory = lambda _client: failed
    result = listen_market_quotes(client, "CON.F.US.MES.Z26", 20, sleep=lambda _seconds: None)
    assert result["ok"] is False
    assert failed.stopped is True
    assert session.calls == []


def test_identify_50k_combine_ignores_150k_and_practice():
    practice = AccountInfo(id=1, name="PRAC-V2-1", balance=150000, simulated=True)
    large = AccountInfo(id=2, name="150KTC-V2-1", balance=150000, simulated=True)
    combine = AccountInfo(id=3, name="50KTC-V2-999", balance=50000, simulated=True)
    word = AccountInfo(id=4, name="50K COMBINE", balance=50000, simulated=None)
    assert [item.id for item in identify_50k_combines([practice, large, combine, word])] == [3, 4]


def test_unsorted_bars():
    order = classify_bar_order(
        [
            {"t": "2026-10-07T14:00:00Z"},
            {"t": "2026-10-07T13:00:00Z"},
            {"t": "2026-10-07T15:00:00Z"},
        ]
    )
    assert order == "unsorted"
    assert classify_bar_order([{"t": "2026-10-07T14:00:00Z"}]) == "too-few-bars"


def test_cli_check_is_wired(monkeypatch, capsys):
    seen = {}

    def fake_run(config, **kwargs):
        seen["skip"] = kwargs["skip_signalr"]
        seen["seconds"] = kwargs["signalr_seconds"]
        print("OVERALL PASS")
        return 0

    monkeypatch.setattr("topstepbot.cli.run_check", fake_run)
    code = main(["check", "--no-signalr", "--signalr-seconds", "5"])
    assert code == 0
    assert seen == {"skip": True, "seconds": 5.0}
    assert "OVERALL PASS" in capsys.readouterr().out

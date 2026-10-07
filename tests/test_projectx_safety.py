from datetime import datetime, timezone

from topstepbot.broker.accounts import assert_account_allowed, looks_live_funded
from topstepbot.broker.base import AccountRejected, ProtectiveStopRequired
from topstepbot.broker.projectx import ProjectXClient, build_place_payload
from topstepbot.config import LIVE_FUNDED_OVERRIDE, load_config
from topstepbot.models import AccountInfo, BracketLeg, Side

CONFIG = load_config("config/settings.yaml")


def _account(**kwargs) -> AccountInfo:
    base = dict(id=7, name="PRAC-50K", can_trade=True, is_visible=True, simulated=True)
    base.update(kwargs)
    return AccountInfo(**base)


def test_live_funded_account_is_refused():
    live = _account(name="LIVE-50K", simulated=False)
    assert looks_live_funded(live)
    try:
        assert_account_allowed(live, CONFIG)
        raise AssertionError("expected a refusal")
    except AccountRejected as exc:
        assert "Live Funded" in str(exc)


def test_override_must_be_the_exact_phrase():
    live = _account(name="LIVE-50K", simulated=False)
    wrong = _with_override("yes")
    try:
        assert_account_allowed(live, wrong)
        raise AssertionError("expected a refusal")
    except AccountRejected:
        pass
    allowed = _with_override(LIVE_FUNDED_OVERRIDE)
    reason = assert_account_allowed(live, allowed)
    assert "override" in reason


def test_practice_and_combine_names_pass_when_simulated_flag_is_missing():
    practice = _account(name="PRACTICE-50K", simulated=None)
    assert "practice" in assert_account_allowed(practice, CONFIG)
    combine_cfg = _with_kind("combine")
    combine = _account(name="50KTC-12345", simulated=None)
    assert "combine" in assert_account_allowed(combine, combine_cfg)


def test_unknown_account_without_simulated_flag_is_refused():
    mystery = _account(name="TEST_ACCOUNT_1", simulated=None)
    try:
        assert_account_allowed(mystery, CONFIG)
        raise AssertionError("expected a refusal")
    except AccountRejected:
        pass


def test_place_payload_always_includes_a_protective_stop():
    leg = BracketLeg(
        tag="A-long-t1-1",
        side=Side.LONG,
        qty=2,
        entry_type="stop",
        entry_price=5650,
        stop_price=5646,
        target_price=5660,
        setup="A",
    )
    payload = build_place_payload(account_id=1, contract_id="CON.F.US.MES.H26", leg=leg, tick_size=0.25)
    assert payload["stopLossBracket"]["type"] == 4
    assert payload["stopLossBracket"]["ticks"] == 16
    assert payload["takeProfitBracket"]["ticks"] == 40
    naked = BracketLeg(
        tag="naked",
        side=Side.LONG,
        qty=1,
        entry_type="market",
        entry_price=5650,
        stop_price=5650,
        target_price=5660,
        setup="A",
    )
    try:
        build_place_payload(account_id=1, contract_id="CON.F.US.MES.H26", leg=naked, tick_size=0.25)
        raise AssertionError("expected a refusal")
    except ProtectiveStopRequired:
        pass


def test_login_failure_does_not_echo_the_api_key(capsys):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"success": False, "errorCode": 3, "token": None, "errorMessage": None}

    class FakeSession:
        def post(self, url, json, headers, timeout):
            assert "apiKey" in json
            self.body = json
            return FakeResponse()

    client = ProjectXClient("trader", "super-secret-key", session=FakeSession())
    try:
        client.login()
    except Exception as exc:
        text = str(exc)
    else:
        raise AssertionError("expected login to fail")
    captured = capsys.readouterr()
    assert "super-secret-key" not in text
    assert "super-secret-key" not in captured.out
    assert "super-secret-key" not in captured.err


def test_rejected_bracket_cancels_the_order_id():
    calls = []

    class FakeResponse:
        def __init__(self, body):
            self.status_code = 200
            self._body = body

        def json(self):
            return self._body

    class FakeSession:
        def post(self, url, json, headers, timeout):
            calls.append((url, json))
            if url.endswith("/api/Auth/loginKey"):
                return FakeResponse({"success": True, "errorCode": 0, "token": "session-token", "errorMessage": None})
            if url.endswith("/api/Order/place"):
                return FakeResponse(
                    {
                        "success": False,
                        "errorCode": 2,
                        "orderId": 99,
                        "errorMessage": "Brackets cannot be used with Position Brackets. You must enable Auto OCO Brackets.",
                    }
                )
            if url.endswith("/api/Order/cancel"):
                return FakeResponse({"success": True, "errorCode": 0, "errorMessage": None})
            raise AssertionError(url)

    client = ProjectXClient("trader", "key", session=FakeSession())
    try:
        client.place_order(
            {
                "accountId": 1,
                "contractId": "CON.F.US.MES.H26",
                "type": 2,
                "side": 0,
                "size": 1,
                "stopLossBracket": {"ticks": 4, "type": 4},
            }
        )
        raise AssertionError("expected rejection")
    except Exception:
        pass
    cancel = [payload for url, payload in calls if url.endswith("/api/Order/cancel")]
    assert cancel == [{"accountId": 1, "orderId": 99}]
    assert all("key" != payload.get("apiKey") or url.endswith("loginKey") for url, payload in calls)


def _with_override(value: str):
    from dataclasses import replace

    return replace(CONFIG, compliance=replace(CONFIG.compliance, allow_live_funded_account_override=value))


def _with_kind(kind: str):
    from dataclasses import replace

    return replace(CONFIG, account=replace(CONFIG.account, kind=kind))

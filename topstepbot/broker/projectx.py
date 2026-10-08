"""ProjectX Gateway API client and broker adapter.

Verified against the public docs at https://gateway.docs.projectx.com on
2026-10-07 (TopstepX connection URLs):

- POST /api/Auth/loginKey with userName + apiKey, bearer token for 24 hours
- POST /api/Auth/validate
- POST /api/Account/search {"onlyActiveAccounts": true}
- POST /api/Contract/available and POST /api/Contract/search
- POST /api/History/retrieveBars (max 20,000 bars; newest-first in the example)
- POST /api/Order/place with stopLossBracket / takeProfitBracket
- POST /api/Order/cancel, /api/Order/modify, /api/Order/searchOpen
- POST /api/Position/searchOpen, /api/Position/closeContract
- POST /api/Trade/search
- SignalR user hub wss://rtc.topstepx.com/hubs/user
- SignalR market hub wss://rtc.topstepx.com/hubs/market
  (docs write https; the WebSocket client uses wss and skipNegotiation)

A live read-only check on 2026-10-07 confirmed Account/search fields
(simulated, canTrade, balance), the MES front month
(CON.F.US.MES.Z26 / MESZ6 / F.US.MES, tick 0.25, tick value 1.25), and that
retrieveBars returns newest-first. Items still marked TODO-VERIFY were not
confirmed by a live call. This repo does not ship credentials.

Bracket mode: the account must be set to Auto OCO Brackets in TopstepX
(Settings > Risk Settings). In Position Brackets mode the API rejects bracket
fields and, per the docs, still creates the order. This client cancels that
order id immediately.

Rate limits (https://gateway.docs.projectx.com/docs/getting-started/rate-limits/):
retrieveBars is 50 calls per 30 seconds and every other route is 200 calls
per 60 seconds. This client waits for a free slot, backs off on HTTP 429,
honors a numeric Retry-After, and raises ApiHalted instead of retrying forever.
Order/place is not retried after a 429, because a second post can duplicate
the order. Reads such as Trade/search are retried until the halt threshold.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests

from topstepbot.broker.base import ProtectiveStopRequired
from topstepbot.broker.ratelimit import (
    HISTORY_LIMIT,
    OTHER_LIMIT,
    ErrorFold,
    RateHalt,
    SlidingWindowLimiter,
)
from topstepbot.config import BotConfig
from topstepbot.models import AccountInfo, Bar, BracketLeg, Fill, Side

log = logging.getLogger("topstepbot.projectx")

# CME month codes used in front-month names such as MESH6.
_MES_NAME = re.compile(r"^MES[FGHJKMNQUVXZ]\d{1,2}$", re.IGNORECASE)

ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 2
ORDER_TYPE_STOP = 4
SIDE_BUY = 0
SIDE_SELL = 1


class ProjectXError(RuntimeError):
    def __init__(self, message: str, error_code: int | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class ApiHalted(ProjectXError):
    """Rate limits or API errors persisted. Callers must stop, not retry in a loop."""


# Sending these again after HTTP 429 can duplicate a fill. Reads are retried; these are not.
_NO_RETRY_PATHS = frozenset(
    {
        "/api/Order/place",
        "/api/Order/modify",
        "/api/Position/closeContract",
        "/api/Position/partialCloseContract",
    }
)


class ProjectXClient:
    def __init__(
        self,
        username: str,
        api_key: str,
        api_url: str = "https://api.topstepx.com",
        user_hub_url: str = "https://rtc.topstepx.com/hubs/user",
        market_hub_url: str = "https://rtc.topstepx.com/hubs/market",
        session: requests.Session | None = None,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        jitter: Callable[[], float] | None = None,
        history_limit: tuple[int, float] = HISTORY_LIMIT,
        other_limit: tuple[int, float] = OTHER_LIMIT,
        max_consecutive_failures: int = 5,
        failure_window_seconds: float = 30.0,
    ) -> None:
        if not username or not api_key:
            raise ProjectXError("PROJECTX_USERNAME and PROJECTX_API_KEY are required")
        self.username = username
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.user_hub_url = user_hub_url
        self.market_hub_url = market_hub_url
        self.http = session or requests.Session()
        self.token: str | None = None
        self.token_acquired_at: datetime | None = None
        self._sleep = sleep or time.sleep
        self.limiter = SlidingWindowLimiter(
            history_limit=history_limit,
            other_limit=other_limit,
            clock=clock or time.monotonic,
            sleep=self._sleep,
            jitter=jitter,
            max_consecutive=max_consecutive_failures,
            failure_window=float(failure_window_seconds),
        )

    def login(self) -> None:
        # The body contains the API key. It is never logged.
        payload = {"userName": self.username, "apiKey": self.api_key}
        body = self._post("/api/Auth/loginKey", payload, auth=False)
        if not body.get("success") or body.get("errorCode") not in (0, None) or not body.get("token"):
            code = body.get("errorCode")
            raise ProjectXError(
                f"ProjectX login failed (errorCode {code}). Check the username and API key.",
                code if isinstance(code, int) else None,
            )
        self.token = str(body["token"])
        self.token_acquired_at = datetime.now(timezone.utc)
        log.info("ProjectX login succeeded")

    def validate(self) -> None:
        """Refresh the session token. Documented: POST /api/Auth/validate."""
        body = self._post("/api/Auth/validate", {}, auth=True)
        if body.get("success") and body.get("newToken"):
            self.token = str(body["newToken"])
            self.token_acquired_at = datetime.now(timezone.utc)
            log.info("ProjectX session validated")
            return
        self.login()

    def search_accounts(self, only_active: bool = True) -> list[AccountInfo]:
        body = self._post("/api/Account/search", {"onlyActiveAccounts": only_active})
        self._raise_if_failed(body, "account search")
        accounts = []
        for raw in body.get("accounts") or []:
            accounts.append(
                AccountInfo(
                    id=int(raw["id"]),
                    name=str(raw.get("name") or ""),
                    can_trade=raw.get("canTrade"),
                    is_visible=raw.get("isVisible"),
                    balance=_optional_float(raw.get("balance")) if "balance" in raw else None,
                    # Confirmed 2026-10-07: Account/search returned simulated and balance.
                    simulated=raw.get("simulated") if "simulated" in raw else None,
                    raw=dict(raw),
                )
            )
        return accounts

    def available_contracts(self, live: bool = False) -> list[dict]:
        body = self._post("/api/Contract/available", {"live": live})
        self._raise_if_failed(body, "available contracts")
        return list(body.get("contracts") or [])

    def search_contracts(self, search_text: str, live: bool = False) -> list[dict]:
        body = self._post("/api/Contract/search", {"searchText": search_text, "live": live})
        self._raise_if_failed(body, "contract search")
        return list(body.get("contracts") or [])

    def retrieve_bars(
        self,
        contract_id: str,
        start: datetime,
        end: datetime,
        *,
        unit: int = 2,
        unit_number: int = 1,
        limit: int = 20000,
        live: bool = False,
        include_partial: bool = False,
    ) -> list[Bar]:
        """Return bars oldest-first.

        Confirmed 2026-10-07 on the Combine: retrieveBars sends newest bars
        first. This sorts ascending so the strategy never sees the future
        first. `limit` cannot exceed 20,000.
        """
        if limit > 20000:
            limit = 20000
        payload = {
            "contractId": contract_id,
            "live": live,
            "startTime": _iso(start),
            "endTime": _iso(end),
            "unit": unit,
            "unitNumber": unit_number,
            "limit": limit,
            "includePartialBar": include_partial,
        }
        body = self._post("/api/History/retrieveBars", payload)
        self._raise_if_failed(body, "retrieve bars")
        bars: list[Bar] = []
        for raw in body.get("bars") or []:
            moment = datetime.fromisoformat(str(raw["t"]).replace("Z", "+00:00"))
            bars.append(
                Bar(
                    time=moment,
                    open=float(raw["o"]),
                    high=float(raw["h"]),
                    low=float(raw["l"]),
                    close=float(raw["c"]),
                    volume=float(raw.get("v") or 0),
                )
            )
        bars.sort(key=lambda bar: bar.time)
        return bars

    def place_order(self, payload: dict) -> int:
        if not payload.get("stopLossBracket"):
            raise ProtectiveStopRequired("refusing to place an order without a stopLossBracket")
        body = self._post("/api/Order/place", payload)
        if body.get("success") and body.get("errorCode") in (0, None) and body.get("orderId") is not None:
            return int(body["orderId"])
        message = str(body.get("errorMessage") or "order rejected")
        order_id = body.get("orderId")
        # Docs: a bracket rejected under Position Brackets mode still creates the order.
        if order_id:
            log.error("Order %s was rejected (%s). Cancelling it so it cannot rest without a stop.", order_id, message)
            try:
                self.cancel_order(int(payload["accountId"]), int(order_id))
            except ApiHalted:
                raise
            except ProjectXError as exc:
                log.error("Could not cancel rejected order %s: %s", order_id, exc)
        raise ProjectXError(message, body.get("errorCode") if isinstance(body.get("errorCode"), int) else None)

    def cancel_order(self, account_id: int, order_id: int) -> None:
        body = self._post("/api/Order/cancel", {"accountId": account_id, "orderId": order_id})
        self._raise_if_failed(body, "cancel order")

    def modify_order(
        self,
        account_id: int,
        order_id: int,
        *,
        size: int | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> None:
        payload: dict = {"accountId": account_id, "orderId": order_id}
        if size is not None:
            payload["size"] = size
        if limit_price is not None:
            payload["limitPrice"] = limit_price
        if stop_price is not None:
            payload["stopPrice"] = stop_price
        body = self._post("/api/Order/modify", payload)
        self._raise_if_failed(body, "modify order")

    def search_open_orders(self, account_id: int) -> list[dict]:
        body = self._post("/api/Order/searchOpen", {"accountId": account_id})
        self._raise_if_failed(body, "open orders")
        return list(body.get("orders") or [])

    def search_open_positions(self, account_id: int) -> list[dict]:
        body = self._post("/api/Position/searchOpen", {"accountId": account_id})
        self._raise_if_failed(body, "open positions")
        return list(body.get("positions") or [])

    def close_contract(self, account_id: int, contract_id: str) -> None:
        body = self._post("/api/Position/closeContract", {"accountId": account_id, "contractId": contract_id})
        self._raise_if_failed(body, "close position")

    def partial_close(self, account_id: int, contract_id: str, size: int) -> None:
        # TODO-VERIFY: community clients call POST /api/Position/partialCloseContract
        # with accountId, contractId, and size. The docs index lists "Partially
        # Close Positions" but the fetched page did not include the schema.
        body = self._post(
            "/api/Position/partialCloseContract",
            {"accountId": account_id, "contractId": contract_id, "size": size},
        )
        self._raise_if_failed(body, "partial close")

    def search_trades(self, account_id: int, start: datetime, end: datetime | None = None) -> list[dict]:
        payload: dict = {"accountId": account_id, "startTimestamp": _iso(start)}
        if end is not None:
            payload["endTimestamp"] = _iso(end)
        body = self._post("/api/Trade/search", payload)
        self._raise_if_failed(body, "trade search")
        return list(body.get("trades") or [])

    def connect_market_hub(self, contract_id: str, on_trade: Callable, on_quote: Callable | None = None):
        """Subscribe to market trades and quotes on wss://rtc.topstepx.com/hubs/market.

        Subscriptions are sent inside the handshake task. The returned client
        does not log the URL, because the URL contains the access token.
        """
        from topstepbot.broker.signalr import JsonSignalRClient

        if not self.token:
            self.login()
        handlers: dict = {"GatewayTrade": lambda *args: on_trade(*args)}
        subscriptions = [("SubscribeContractTrades", [contract_id])]
        if on_quote is not None:
            handlers["GatewayQuote"] = lambda *args: on_quote(*args)
            subscriptions.insert(0, ("SubscribeContractQuotes", [contract_id]))
        hub = JsonSignalRClient(self.market_hub_url, self.token, handlers, subscriptions)
        hub.start()
        return hub

    def connect_user_hub(self, account_id: int, on_account: Callable, on_order: Callable, on_position: Callable, on_trade: Callable):
        """User hub. Same WebSocket client as the market hub. Does not log the token."""
        from topstepbot.broker.signalr import JsonSignalRClient

        if not self.token:
            self.login()
        handlers = {
            "GatewayUserAccount": on_account,
            "GatewayUserOrder": on_order,
            "GatewayUserPosition": on_position,
            "GatewayUserTrade": on_trade,
        }
        subscriptions = [
            ("SubscribeAccounts", []),
            ("SubscribeOrders", [account_id]),
            ("SubscribePositions", [account_id]),
            ("SubscribeTrades", [account_id]),
        ]
        hub = JsonSignalRClient(self.user_hub_url, self.token, handlers, subscriptions)
        hub.start()
        return hub

    def allow_halt_probe(self, count: int = 2) -> None:
        """After a halt, allow a few reads so the open book can be printed once."""
        self.limiter.allow_halt_probe(count)

    def _post(self, path: str, payload: dict, auth: bool = True) -> dict:
        if self.limiter.halted:
            return self._probe(path, payload, auth)
        refreshed = False
        while True:
            if auth and (self.token is None or self._token_is_old()):
                # login() posts with auth=False, so this does not recurse.
                # The API key stays in that body and is not logged.
                self.token = None
                self.login()
            self._acquire(path)
            response = self._send(f"{self.api_url}{path}", payload, self._headers(auth))
            if response.status_code == 401 and auth and not refreshed:
                refreshed = True
                self.token = None
                self.login()
                continue
            if response.status_code == 429 or response.status_code >= 500:
                delay = self._backoff(response)
                if path in _NO_RETRY_PATHS:
                    # A retry can create a second order. Wait, then stop this call.
                    self._sleep(delay)
                    raise ProjectXError(
                        f"ProjectX HTTP {response.status_code} on {path}. The request was not retried."
                    )
                self._sleep(delay)
                continue
            self.limiter.note_success()
            return self._parse(response, path)

    def _probe(self, path: str, payload: dict, auth: bool) -> dict:
        if not self.limiter.consume_probe():
            raise ApiHalted(self.limiter.halt_reason or "ProjectX API halted. Trading stopped.")
        response = self._send(f"{self.api_url}{path}", payload, self._headers(auth))
        if response.status_code >= 400:
            raise ProjectXError(f"ProjectX HTTP {response.status_code} on {path}")
        return self._parse(response, path)

    def _acquire(self, path: str) -> None:
        try:
            self.limiter.before_request(path)
        except RateHalt as exc:
            raise ApiHalted(str(exc)) from exc

    def _backoff(self, response: requests.Response) -> float:
        try:
            return self.limiter.note_failure(_retry_after(response))
        except RateHalt as exc:
            raise ApiHalted(str(exc)) from exc

    def _headers(self, auth: bool) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _parse(response: requests.Response, path: str) -> dict:
        if response.status_code >= 400:
            raise ProjectXError(f"ProjectX HTTP {response.status_code} on {path}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProjectXError(f"ProjectX returned a non-JSON body on {path}") from exc
        if not isinstance(body, dict):
            raise ProjectXError(f"ProjectX returned an unexpected body on {path}")
        return body

    def _send(self, url: str, payload: dict, headers: dict) -> requests.Response:
        # Do not log headers or payload: they can contain the bearer token or API key.
        return self.http.post(url, json=payload, headers=headers, timeout=30)

    def _token_is_old(self) -> bool:
        if self.token_acquired_at is None:
            return False
        return datetime.now(timezone.utc) - self.token_acquired_at > timedelta(hours=23)

    @staticmethod
    def _raise_if_failed(body: dict, what: str) -> None:
        if body.get("success") and body.get("errorCode") in (0, None):
            return
        code = body.get("errorCode")
        message = body.get("errorMessage") or f"{what} failed"
        raise ProjectXError(str(message), code if isinstance(code, int) else None)


def build_place_payload(
    *,
    account_id: int,
    contract_id: str,
    leg: BracketLeg,
    tick_size: float,
) -> dict:
    """Build an Order/place body. Always includes a protective stop bracket."""
    if leg.qty < 1:
        raise ProtectiveStopRequired("qty must be at least 1")
    if leg.entry_price is None:
        raise ProtectiveStopRequired("need an entry price to measure the stop distance")
    stop_ticks = int(round(abs(leg.entry_price - leg.stop_price) / tick_size))
    if stop_ticks < 1:
        raise ProtectiveStopRequired("stop is closer than one tick")
    if leg.side is Side.LONG and leg.stop_price >= leg.entry_price:
        raise ProtectiveStopRequired("long stop must be below the entry")
    if leg.side is Side.SHORT and leg.stop_price <= leg.entry_price:
        raise ProtectiveStopRequired("short stop must be above the entry")
    side = SIDE_BUY if leg.side is Side.LONG else SIDE_SELL
    if leg.entry_type == "market":
        order_type = ORDER_TYPE_MARKET
        stop_price = None
    elif leg.entry_type == "stop":
        order_type = ORDER_TYPE_STOP
        stop_price = leg.entry_price
    else:
        raise ProtectiveStopRequired(f"unsupported entry type {leg.entry_type}")
    target_ticks = None
    if leg.target_price is not None:
        target_ticks = int(round(abs(leg.target_price - leg.entry_price) / tick_size))
        if target_ticks < 1:
            target_ticks = None
    payload = {
        "accountId": account_id,
        "contractId": contract_id,
        "type": order_type,
        "side": side,
        "size": leg.qty,
        "limitPrice": None,
        "stopPrice": stop_price,
        "trailPrice": None,
        "customTag": leg.tag,
        "stopLossBracket": {"ticks": stop_ticks, "type": ORDER_TYPE_STOP},
        "takeProfitBracket": (
            {"ticks": target_ticks, "type": ORDER_TYPE_LIMIT} if target_ticks else None
        ),
    }
    return payload


def pick_front_month(contracts: list[dict], symbol: str = "MES") -> dict:
    """Active front month for MES.

    Confirmed 2026-10-07 from Contract/search on the Combine: the active
    contract was id ``CON.F.US.MES.Z26``, name ``MESZ6``, symbolId ``F.US.MES``,
    tickSize 0.25, tickValue 1.25, activeContract true. The name uses a
    one-digit year (MESZ6); the id uses two (Z26). Matching keeps an active
    contract whose name is MES plus a month code, whose symbolId ends in
    ``.MES``, or whose description says Micro E-mini S&P. The first active
    match in the order Contract/search returned is the one the bot trades.
    """
    symbol = symbol.upper()
    chosen = []
    for contract in contracts:
        if not contract.get("activeContract"):
            continue
        name = str(contract.get("name") or "")
        symbol_id = str(contract.get("symbolId") or "")
        description = str(contract.get("description") or "").lower()
        if symbol == "MES" and (
            _MES_NAME.match(name)
            or symbol_id.upper().endswith(".MES")
            or "micro e-mini s&p" in description
        ):
            chosen.append(contract)
    if not chosen:
        raise ProjectXError(
            f"No active {symbol} contract in the ProjectX contract list. "
            "Check Contract/search and set instrument.contract_id if the naming differs."
        )
    return chosen[0]


def _retry_after(response: requests.Response) -> float | None:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    raw = getter("Retry-After")
    if raw is None:
        raw = getter("retry-after")
    if raw is None or raw == "":
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ProjectXError("timestamps sent to ProjectX must include a timezone")
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


class ProjectXBroker:
    """Broker adapter. Places real orders. Only construct this from practice/live mode."""

    name = "projectx"

    def __init__(self, config: BotConfig, client: ProjectXClient, account: AccountInfo, contract: dict) -> None:
        self.config = config
        self.client = client
        self.account = account
        self.contract = contract
        self.contract_id = str(contract["id"])
        self.tick_size = float(contract.get("tickSize") or config.instrument.tick_size)
        self.last_price: float | None = None
        self._groups: dict[str, dict] = {}
        self._seq = 0
        self._seen_trades: set[int] = set()
        self._trade_cursor = datetime.now(timezone.utc) - timedelta(minutes=5)
        # Historical warmup sets this false so a replayed 10:30 flatten cannot hit the API.
        self.orders_enabled = True
        self._errors = ErrorFold(lambda message: log.error("%s", message))

    def place_brackets(self, legs: list[BracketLeg]) -> list[str]:
        if not self.orders_enabled:
            raise ProjectXError("Orders are suspended. Refusing to place brackets.")
        ids = []
        for index, leg in enumerate(legs):
            if index > 0:
                # Two legs are one decision (half and runner), not two strategies.
                time.sleep(0.2)
            payload = build_place_payload(
                account_id=self.account.id,
                contract_id=self.contract_id,
                leg=leg,
                tick_size=self.tick_size,
            )
            order_id = self.client.place_order(payload)
            self._seq += 1
            group_id = f"px-{self._seq}"
            self._groups[group_id] = {
                "leg": leg,
                "entry_order_id": order_id,
                "stop_price": leg.stop_price,
                "target_price": leg.target_price,
                "qty_open": 0,
                "entry_price": 0.0,
                "realized": 0.0,
                "closed": False,
                "exit_reason": "",
                "entry_filled": False,
            }
            ids.append(group_id)
            log.info("Placed %s order %s qty %s stop ticks included", leg.tag, order_id, leg.qty)
        return ids

    def on_bar(self, bar: Bar) -> list[Fill]:
        """Remember the price. Fills come from poll(), once per live cycle.

        Calling Trade/search here walked every historical warmup minute and
        produced the HTTP 429 flood. The paper broker still fills inside on_bar.
        """
        self.last_price = bar.close
        return []

    def poll(self, when: datetime) -> list[Fill]:
        """Turn new Trade/search rows into fills. Idempotent. One call per live cycle."""
        if not self.orders_enabled:
            return []
        try:
            trades = self.client.search_trades(self.account.id, self._trade_cursor, when.astimezone(timezone.utc))
        except ApiHalted:
            raise
        except ProjectXError as exc:
            self._errors.report(f"Trade search failed: {exc}")
            return []
        fills: list[Fill] = []
        for trade in trades:
            trade_id = int(trade["id"])
            if trade_id in self._seen_trades:
                continue
            self._seen_trades.add(trade_id)
            if trade.get("voided"):
                continue
            if str(trade.get("contractId")) != self.contract_id:
                continue
            fill = self._fill_from_trade(trade, when)
            if fill is not None:
                fills.append(fill)
        return fills

    def cancel_all(self) -> None:
        if not self.orders_enabled:
            return
        try:
            orders = self.client.search_open_orders(self.account.id)
        except ApiHalted:
            raise
        except ProjectXError as exc:
            self._errors.report(f"Could not list open orders: {exc}")
            orders = []
        for order in orders:
            if str(order.get("contractId")) != self.contract_id:
                continue
            try:
                self.client.cancel_order(self.account.id, int(order["id"]))
            except ApiHalted:
                raise
            except ProjectXError as exc:
                self._errors.report(f"Cancel {order.get('id')} failed: {exc}")
        for group in self._groups.values():
            if not group["entry_filled"] and not group["closed"]:
                group["closed"] = True
                group["exit_reason"] = "cancelled"

    def flatten(self, price: float, when: datetime, reason: str = "flatten") -> list[Fill]:
        self.last_price = price
        if not self.orders_enabled:
            return []
        log.info("Flatten requested (%s)", reason)
        # Cancel working orders even when the close call fails.
        close_error: ProjectXError | None = None
        try:
            positions = self.client.search_open_positions(self.account.id)
        except ApiHalted:
            raise
        except ProjectXError as exc:
            self._errors.report(f"Position search failed during flatten: {exc}")
            positions = []
        for position in positions:
            if str(position.get("contractId")) != self.contract_id:
                continue
            if int(position.get("size") or 0) <= 0:
                continue
            try:
                self.client.close_contract(self.account.id, self.contract_id)
            except ApiHalted:
                raise
            except ProjectXError as exc:
                self._errors.report(f"Close position failed: {exc}")
                close_error = exc
        self.cancel_all()
        fills = self.poll(when)
        if close_error is not None:
            raise close_error
        return fills

    def modify_stop(self, group_id: str, new_stop: float) -> bool:
        if not self.orders_enabled:
            return False
        group = self._groups.get(group_id)
        if group is None or group["closed"] or group["qty_open"] <= 0:
            return False
        leg: BracketLeg = group["leg"]
        old = group["stop_price"]
        if leg.side is Side.LONG and new_stop <= old + 1e-9:
            return False
        if leg.side is Side.SHORT and new_stop >= old - 1e-9:
            return False
        stop_id = group.get("stop_order_id")
        if stop_id is None:
            stop_id = self._find_stop_order(leg)
            group["stop_order_id"] = stop_id
        if stop_id is None:
            log.error("No working protective stop found for %s. Flattening is safer than trailing blind.", group_id)
            return False
        self.client.modify_order(self.account.id, int(stop_id), stop_price=new_stop)
        group["stop_price"] = new_stop
        return True

    def net_qty(self) -> int:
        total = 0
        for group in self._groups.values():
            if group["qty_open"] <= 0:
                continue
            sign = 1 if group["leg"].side is Side.LONG else -1
            total += sign * group["qty_open"]
        return total

    def average_price(self) -> float | None:
        qty = 0
        notion = 0.0
        for group in self._groups.values():
            if group["qty_open"] <= 0:
                continue
            qty += group["qty_open"]
            notion += group["entry_price"] * group["qty_open"]
        if qty == 0:
            return None
        return notion / qty

    def unrealized(self, price: float) -> float:
        point_value = self.config.dollars_per_point
        total = 0.0
        for group in self._groups.values():
            if group["qty_open"] <= 0:
                continue
            side = group["leg"].side
            points = (price - group["entry_price"]) if side is Side.LONG else (group["entry_price"] - price)
            total += points * point_value * group["qty_open"]
        return total

    def open_risk(self) -> float:
        point_value = self.config.dollars_per_point
        total = 0.0
        for group in self._groups.values():
            if group["qty_open"] <= 0:
                continue
            distance = abs(group["entry_price"] - group["stop_price"])
            total += distance * point_value * group["qty_open"]
        return total

    def working_entry_count(self) -> int:
        return sum(1 for group in self._groups.values() if not group["entry_filled"] and not group["closed"])

    def group_open(self, group_id: str) -> bool:
        group = self._groups.get(group_id)
        return bool(group and group["qty_open"] > 0 and not group["closed"])

    def group_realized(self, group_id: str) -> float:
        return float(self._groups[group_id]["realized"])

    def group_closed(self, group_id: str) -> bool:
        return bool(self._groups[group_id]["closed"])

    def group_exit_reason(self, group_id: str) -> str:
        return str(self._groups[group_id]["exit_reason"])

    def stop_price(self, group_id: str) -> float | None:
        group = self._groups.get(group_id)
        if group is None:
            return None
        return group["stop_price"]

    def cancel_entry(self, group_id: str) -> bool:
        if not self.orders_enabled:
            return False
        group = self._groups.get(group_id)
        if group is None or group["entry_filled"] or group["closed"]:
            return False
        self.client.cancel_order(self.account.id, int(group["entry_order_id"]))
        group["closed"] = True
        group["exit_reason"] = "cancelled"
        return True

    def _fill_from_trade(self, trade: dict, when: datetime) -> Fill | None:
        order_id = int(trade.get("orderId") or 0)
        group_id = None
        group = None
        for gid, candidate in self._groups.items():
            if int(candidate["entry_order_id"]) == order_id:
                group_id = gid
                group = candidate
                break
        price = float(trade["price"])
        qty = int(trade["size"])
        side_code = int(trade.get("side") or 0)
        order_side = Side.LONG if side_code == SIDE_BUY else Side.SHORT
        fee = float(trade.get("fees") or 0)
        moment_raw = trade.get("creationTimestamp")
        moment = datetime.fromisoformat(str(moment_raw).replace("Z", "+00:00")) if moment_raw else when
        if group is not None and group_id is not None and not group["entry_filled"]:
            group["entry_filled"] = True
            group["qty_open"] = qty
            group["entry_price"] = price
            group["realized"] -= fee
            return Fill(
                order_id=str(order_id),
                tag=group["leg"].tag,
                group=group_id,
                role="entry",
                side=group["leg"].side,
                qty=qty,
                price=price,
                time=moment,
                fee=fee,
            )
        # An exit trade. Attach it to the open group on the opposite side.
        # TODO-VERIFY: child bracket order ids are not returned by Order/place.
        for gid, candidate in self._groups.items():
            if candidate["qty_open"] <= 0 or candidate["closed"]:
                continue
            if candidate["leg"].side is order_side:
                continue
            role = "target"
            target = candidate["target_price"]
            stop = candidate["stop_price"]
            if target is None or abs(price - stop) <= abs(price - target):
                role = "stop"
            points = (
                price - candidate["entry_price"]
                if candidate["leg"].side is Side.LONG
                else candidate["entry_price"] - price
            )
            pnl = points * self.config.dollars_per_point * qty
            reported = trade.get("profitAndLoss")
            if reported is not None:
                pnl = float(reported)
            candidate["realized"] += pnl - fee
            candidate["qty_open"] = max(0, candidate["qty_open"] - qty)
            if candidate["qty_open"] == 0:
                candidate["closed"] = True
                candidate["exit_reason"] = role
            return Fill(
                order_id=str(order_id),
                tag=candidate["leg"].tag,
                group=gid,
                role=role,
                side=order_side,
                qty=qty,
                price=price,
                time=moment,
                fee=fee,
            )
        return None

    def _find_stop_order(self, leg: BracketLeg) -> int | None:
        protective = SIDE_SELL if leg.side is Side.LONG else SIDE_BUY
        orders = self.client.search_open_orders(self.account.id)
        for order in orders:
            if str(order.get("contractId")) != self.contract_id:
                continue
            if int(order.get("type") or 0) != ORDER_TYPE_STOP:
                continue
            if int(order.get("side") or -1) != protective:
                continue
            return int(order["id"])
        return None

"""ProjectX client that can only read. It cannot place, change, or cancel orders.

The HTTP layer allows four paths and nothing else:

- POST /api/Auth/loginKey
- POST /api/Account/search
- POST /api/Contract/search
- POST /api/History/retrieveBars

Order methods exist so a caller cannot miss the restriction, and each one
raises before any socket is opened. A path outside the allowlist raises the
same way, including every /api/Order/* and position-close route. The check
command uses this class and never constructs ProjectXClient.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import requests

from topstepbot.broker.projectx import ProjectXError, _retry_after
from topstepbot.models import AccountInfo

# Exact paths. Anything else, including /api/Order/place, is refused.
READ_ONLY_PATHS = frozenset(
    {
        "/api/Auth/loginKey",
        "/api/Account/search",
        "/api/Contract/search",
        "/api/History/retrieveBars",
    }
)

_DISABLED = (
    "place_order",
    "cancel_order",
    "modify_order",
    "search_open_orders",
    "close_contract",
    "partial_close",
    "search_open_positions",
    "search_trades",
)


class ReadOnlyViolation(RuntimeError):
    """Raised when the check client is asked to do anything except read."""


class ReadOnlyProjectXClient:
    """Read-only wrapper. Order methods are disabled and the HTTP allowlist enforces it."""

    def __init__(
        self,
        username: str,
        api_key: str,
        api_url: str = "https://api.topstepx.com",
        market_hub_url: str = "https://rtc.topstepx.com/hubs/market",
        session: requests.Session | None = None,
    ) -> None:
        if not username or not api_key:
            raise ProjectXError("PROJECTX_USERNAME and PROJECTX_API_KEY are required")
        self.username = username
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.market_hub_url = market_hub_url
        self.http = session or requests.Session()
        self.token: str | None = None
        # Set from a numeric Retry-After when History/retrieveBars returns HTTP 429.
        self.last_retry_after: float | None = None
        # Tests inject a hub factory. Production uses the JSON SignalR client.
        self._hub_factory: Callable[[ReadOnlyProjectXClient], object] | None = None
        self._signalr_connector: Callable | None = None

    def login(self) -> dict:
        """POST /api/Auth/loginKey. Returns the raw JSON body. Does not log the key or token."""
        body = self._post(
            "/api/Auth/loginKey",
            {"userName": self.username, "apiKey": self.api_key},
            auth=False,
        )
        if not body.get("success") or body.get("errorCode") not in (0, None) or not body.get("token"):
            code = body.get("errorCode")
            raise ProjectXError(
                f"ProjectX login failed (errorCode {code}). Check the username and API key.",
                code if isinstance(code, int) else None,
            )
        self.token = str(body["token"])
        return body

    def search_accounts_raw(self, only_active: bool = True) -> dict:
        """POST /api/Account/search. Returns the raw JSON body, unsorted and unfiltered."""
        body = self._post("/api/Account/search", {"onlyActiveAccounts": only_active})
        _raise_if_failed(body, "account search")
        return body

    def search_contracts_raw(self, search_text: str, live: bool = False) -> dict:
        """POST /api/Contract/search. Returns the raw JSON body."""
        body = self._post("/api/Contract/search", {"searchText": search_text, "live": live})
        _raise_if_failed(body, "contract search")
        return body

    def retrieve_bars_raw(
        self,
        contract_id: str,
        start: datetime,
        end: datetime,
        *,
        unit: int = 2,
        unit_number: int = 5,
        limit: int = 400,
        live: bool = False,
        include_partial: bool = False,
    ) -> dict:
        """POST /api/History/retrieveBars. Bars are left in the order the API sent them."""
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
        _raise_if_failed(body, "retrieve bars")
        return body

    def connect_quotes(self, contract_id: str, on_quote: Callable[..., None]):
        """Subscribe to GatewayQuote only. Does not subscribe to trades or user orders.

        The hub URL is wss://rtc.topstepx.com/hubs/market (https in config is
        rewritten). The token stays in the query string and is not logged.
        """
        if not self.token:
            raise ProjectXError("not logged in")
        if self._hub_factory is not None:
            hub = self._hub_factory(self)
            hub.on("GatewayQuote", lambda *args: on_quote(*args))
            try:
                hub.start()
                hub.send("SubscribeContractQuotes", [contract_id])
            except Exception:
                stop = getattr(hub, "stop", None)
                if callable(stop):
                    try:
                        stop()
                    except Exception:
                        pass
                raise
            return hub
        from topstepbot.broker.signalr import JsonSignalRClient

        hub = JsonSignalRClient(
            self.market_hub_url,
            self.token,
            {"GatewayQuote": lambda *args: on_quote(*args)},
            [("SubscribeContractQuotes", [contract_id])],
            connector=self._signalr_connector,
        )
        try:
            hub.start()
        except Exception:
            hub.stop()
            raise
        return hub

    def place_order(self, payload: dict | None = None) -> int:
        raise self._disabled("place_order")

    def cancel_order(self, account_id: int = 0, order_id: int = 0) -> None:
        raise self._disabled("cancel_order")

    def modify_order(self, account_id: int = 0, order_id: int = 0, **kwargs) -> None:
        raise self._disabled("modify_order")

    def search_open_orders(self, account_id: int = 0) -> list:
        raise self._disabled("search_open_orders")

    def close_contract(self, account_id: int = 0, contract_id: str = "") -> None:
        raise self._disabled("close_contract")

    def partial_close(self, account_id: int = 0, contract_id: str = "", size: int = 0) -> None:
        raise self._disabled("partial_close")

    def search_open_positions(self, account_id: int = 0) -> list:
        raise self._disabled("search_open_positions")

    def search_trades(self, account_id: int = 0, *args, **kwargs) -> list:
        raise self._disabled("search_trades")

    def _disabled(self, name: str) -> ReadOnlyViolation:
        return ReadOnlyViolation(
            f"{name} is disabled. The connection check never places, modifies, or cancels orders."
        )

    def _post(self, path: str, payload: dict, auth: bool = True) -> dict:
        if path not in READ_ONLY_PATHS or "order" in path.lower():
            raise ReadOnlyViolation(f"read-only client refused {path}")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            if not self.token:
                raise ProjectXError("not logged in")
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"{self.api_url}{path}"
        try:
            response = self.http.post(url, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            # The exception text can include the URL. It must not include the API key.
            # The bearer token is only in the header, which requests does not put in the message.
            raise ProjectXError(f"ProjectX request failed on {path} ({type(exc).__name__})") from exc
        if response.status_code == 429:
            self.last_retry_after = _retry_after(response)
            raise ProjectXError("ProjectX rate limit (HTTP 429). Back off and try again.")
        if response.status_code >= 400:
            raise ProjectXError(f"ProjectX HTTP {response.status_code} on {path}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProjectXError(f"ProjectX returned a non-JSON body on {path}") from exc
        if not isinstance(body, dict):
            raise ProjectXError(f"ProjectX returned an unexpected body on {path}")
        return body


def accounts_from_body(body: dict) -> list[AccountInfo]:
    accounts = []
    for raw in body.get("accounts") or []:
        accounts.append(
            AccountInfo(
                id=int(raw["id"]),
                name=str(raw.get("name") or ""),
                can_trade=raw.get("canTrade") if "canTrade" in raw else None,
                is_visible=raw.get("isVisible") if "isVisible" in raw else None,
                balance=_optional_float(raw.get("balance")) if "balance" in raw else None,
                simulated=raw.get("simulated") if "simulated" in raw else None,
                raw=dict(raw),
            )
        )
    return accounts


def _raise_if_failed(body: dict, what: str) -> None:
    if body.get("success") and body.get("errorCode") in (0, None):
        return
    code = body.get("errorCode")
    message = body.get("errorMessage") or f"{what} failed"
    raise ProjectXError(str(message), code if isinstance(code, int) else None)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ProjectXError("timestamps sent to ProjectX must include a timezone")
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)

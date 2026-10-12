"""Minimal SignalR JSON hub client over the `websockets` package.

ProjectX documents the market hub as
``https://rtc.topstepx.com/hubs/market?access_token=JWT`` with
``skipNegotiation: true`` and WebSockets. The JavaScript client rewrites
``https`` to ``wss`` and then sends the SignalR handshake. Subscriptions are
``SubscribeContractQuotes``, ``SubscribeContractTrades``, and
``SubscribeContractMarketDepth``. Events are ``GatewayQuote``,
``GatewayTrade``, and ``GatewayDepth``.

``signalrcore`` 1.0.2 is not used. Its hand-rolled socket reads the WebSocket
frame header with ``SSLSocket.recv(2)``. On Python 3.14 that short read raises
``SSL: BAD_LENGTH`` inside ``_ssl.c``. ``start()`` returns before the reader
thread dies, so a caller can believe the hub is up while the socket is already
closed and no quotes will ever arrive. This client lets ``websockets`` do the
TLS and framing (full-size reads), then speaks the SignalR JSON protocol
itself. Records are separated by ``\\x1e``.

The hub URL that is safe to print never includes the access token.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from topstepbot.broker.projectx import ProjectXError

_RECORD = "\x1e"
_HANDSHAKE = '{"protocol":"json","version":1}' + _RECORD


def public_hub_url(base: str) -> str:
    """WebSocket URL with no query string. Safe to print."""
    text = (base or "").strip()
    if not text:
        raise ProjectXError("market hub URL is empty")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme in {"https", "wss"}:
        scheme = "wss"
    elif scheme in {"http", "ws"}:
        scheme = "ws"
    else:
        raise ProjectXError("market hub URL must start with https:// or wss://")
    if not parts.hostname:
        raise ProjectXError("market hub URL has no host")
    path = parts.path.rstrip("/") or ""
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def connect_hub_url(base: str, token: str) -> str:
    """WebSocket URL carrying the token. Do not log or print this."""
    if not token:
        raise ProjectXError("not logged in")
    return f"{public_hub_url(base)}?access_token={token}"


def invocation(target: str, arguments: list, invocation_id: str | None = None) -> str:
    body: dict = {"type": 1, "target": target, "arguments": list(arguments)}
    if invocation_id is not None:
        body["invocationId"] = invocation_id
    return json.dumps(body, separators=(",", ":")) + _RECORD


def ping_message() -> str:
    return '{"type":6}' + _RECORD


class FrameBuffer:
    """Split a SignalR byte stream on the record separator."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[dict]:
        self._buf += text
        messages = []
        while _RECORD in self._buf:
            raw, self._buf = self._buf.split(_RECORD, 1)
            if not raw.strip():
                continue
            messages.append(json.loads(raw))
        return messages


class JsonSignalRClient:
    """One hub connection. Subscribe immediately after the handshake.

    ProjectX closes the socket if the subscription arrives late. The
    subscriptions passed here are sent before ``start`` returns and before
    the read loop waits on the caller.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        handlers: dict[str, Callable],
        subscriptions: list[tuple[str, list]],
        connector: Callable | None = None,
        on_status: Callable[[str], None] | None = None,
        reconnect_seconds: float = 2.0,
    ) -> None:
        self.public_url = public_hub_url(base_url)
        self._base_url = base_url
        self._token = token
        self._handlers = handlers
        self._subscriptions = list(subscriptions)
        self._connector = connector
        self._on_status = on_status
        self.reconnect_seconds = reconnect_seconds
        self.error: BaseException | None = None
        self._connected = False
        self._user_stop = False
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_async: asyncio.Event | None = None
        self._invocation = 0

    def start(self, timeout: float = 15) -> None:
        self._thread = threading.Thread(target=self._thread_main, name="signalr-json", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.stop()
            raise ProjectXError(f"Market hub handshake timed out ({self.public_url})")
        # A drop after the handshake is a reconnect, not a failed start.
        if self.error is not None and not self._user_stop and not self._connected:
            raise ProjectXError(redact_token(self.error, self._token))

    def stop(self) -> None:
        self._user_stop = True
        loop = self._loop
        flag = self._stop_async
        if loop is not None and flag is not None:
            try:
                loop.call_soon_threadsafe(flag.set)
            except RuntimeError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            if self.error is None and not self._user_stop:
                self.error = exc
        finally:
            self._ready.set()

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_async = asyncio.Event()
        connected_once = False
        delay = max(0.0, self.reconnect_seconds)
        while not self._user_stop and self._stop_async is not None and not self._stop_async.is_set():
            try:
                url = connect_hub_url(self._base_url, self._token)
                connection = self._open(url)
                async with connection as socket:
                    await self._handshake_and_subscribe(socket)
                    self.error = None
                    self._connected = True
                    self._ready.set()
                    if connected_once:
                        self._status("Quote stream is back.")
                    connected_once = True
                    delay = max(0.0, self.reconnect_seconds)
                    await self._read_until_stop(socket)
            except Exception as exc:
                if self.error is None and not self._user_stop:
                    self.error = exc
                self._ready.set()
            if self._user_stop or self._stop_async.is_set() or not connected_once:
                break
            self._status("Quote stream dropped. Reconnecting.")
            try:
                await asyncio.wait_for(self._stop_async.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass
            delay = min(max(delay * 2, 0.05), 30)
            self.error = None

    def _status(self, message: str) -> None:
        callback = self._on_status
        if callback is None:
            return
        try:
            callback(message)
        except Exception:
            return

    def _open(self, url: str):
        if self._connector is not None:
            return self._connector(url)
        import inspect
        import websockets

        kwargs = {"open_timeout": 10, "ping_interval": None, "max_size": 2**20}
        # A proxy would wrap TLS and can surface as a bad TLS record. Connect direct.
        if "proxy" in inspect.signature(websockets.connect).parameters:
            kwargs["proxy"] = None
        return websockets.connect(url, **kwargs)

    async def _handshake_and_subscribe(self, socket) -> None:
        await socket.send(_HANDSHAKE)
        opener = FrameBuffer()
        deadline = time.monotonic() + 10
        seen = False
        extra: list[dict] = []
        while not seen:
            if time.monotonic() > deadline:
                raise ProjectXError(f"Market hub handshake timed out ({self.public_url})")
            incoming = await asyncio.wait_for(socket.recv(), timeout=10)
            messages = opener.feed(_as_text(incoming))
            if not messages:
                continue
            first, *rest = messages
            if "error" in first and "type" not in first:
                raise ProjectXError(f"SignalR handshake rejected: {first.get('error')}")
            extra.extend(rest)
            seen = True
        for target, arguments in self._subscriptions:
            self._invocation += 1
            await socket.send(invocation(target, arguments, str(self._invocation)))
        self._pending_buffer = opener
        self._pending_messages = extra

    async def _read_until_stop(self, socket) -> None:
        buffer: FrameBuffer = getattr(self, "_pending_buffer", FrameBuffer())
        last_ping = time.monotonic()
        assert self._stop_async is not None
        for message in getattr(self, "_pending_messages", []):
            if self._dispatch(message):
                return
        while not self._stop_async.is_set():
            try:
                incoming = await asyncio.wait_for(socket.recv(), timeout=1)
            except asyncio.TimeoutError:
                if time.monotonic() - last_ping >= 10:
                    await socket.send(ping_message())
                    last_ping = time.monotonic()
                continue
            for message in buffer.feed(_as_text(incoming)):
                if self._dispatch(message):
                    return
                if message.get("type") == 6:
                    await socket.send(ping_message())
                    last_ping = time.monotonic()

    def _dispatch(self, message: dict) -> bool:
        """Handle one JSON record. Return True to close the read loop."""
        kind = message.get("type")
        if kind == 7:
            detail = message.get("error") or "market hub closed the connection"
            self.error = ProjectXError(str(detail))
            return True
        if kind == 3 and message.get("error"):
            self.error = ProjectXError(str(message.get("error")))
            return True
        if kind == 1:
            target = str(message.get("target") or "")
            handler = self._handlers.get(target)
            if handler is not None:
                arguments = message.get("arguments") or []
                handler(*arguments)
        return False


def redact_token(error: BaseException, token: str) -> str:
    text = f"{type(error).__name__}: {error}"
    if token and token in text:
        text = text.replace(token, "[REDACTED]")
    if len(text) > 300:
        text = text[:300] + "..."
    return text


def _as_text(incoming) -> str:
    if isinstance(incoming, bytes):
        return incoming.decode("utf-8")
    return str(incoming)


def quote_gate(quote_count: int, market_open: bool) -> str:
    """Decide whether practice mode may arm.

    ``arm`` only after at least one live quote. ``flat-open`` means the MES
    session is open and the feed produced nothing, so the bot must not trade.
    ``flat-closed`` means the session is closed; stay flat without treating
    that as a broken feed.
    """
    if quote_count > 0:
        return "arm"
    if market_open:
        return "flat-open"
    return "flat-closed"

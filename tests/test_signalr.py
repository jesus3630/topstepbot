"""SignalR JSON framing and the quote gate. No network."""

import asyncio
import json
import time

from topstepbot.broker.readonly import ReadOnlyProjectXClient
from topstepbot.broker.signalr import (
    FrameBuffer,
    JsonSignalRClient,
    invocation,
    public_hub_url,
    quote_gate,
)

TOKEN = "super-secret-token"


class ScriptedSocket:
    def __init__(self):
        self.sent = []
        self.queue = asyncio.Queue()
        self.closed = False

    async def send(self, data):
        self.sent.append(data)
        if '"protocol"' in data:
            await self.queue.put("{}\x1e")
        elif "SubscribeContractQuotes" in data:
            quote = {
                "type": 1,
                "target": "GatewayQuote",
                "arguments": [
                    "CON.F.US.MES.Z26",
                    {"lastPrice": 5800.25, "bestBid": 5800.0, "bestAsk": 5800.5},
                ],
            }
            await self.queue.put(json.dumps(quote) + "\x1e")

    async def recv(self):
        return await self.queue.get()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False


def test_public_hub_url_drops_the_token_and_uses_wss():
    url = public_hub_url(f"https://rtc.topstepx.com/hubs/market?access_token={TOKEN}")
    assert url == "wss://rtc.topstepx.com/hubs/market"
    assert TOKEN not in url


def test_frame_buffer_splits_records_and_keeps_a_partial_tail():
    buffer = FrameBuffer()
    assert buffer.feed('{"type":6}') == []
    messages = buffer.feed('\x1e{"type":1,"target":"GatewayQuote","arguments":[1]}\x1e{"type":')
    assert messages[0]["type"] == 6
    assert messages[1]["target"] == "GatewayQuote"
    assert buffer.feed("7}\x1e") == [{"type": 7}]


def test_invocation_is_a_signalr_json_record():
    raw = invocation("SubscribeContractQuotes", ["CON.F.US.MES.Z26"], "1")
    assert raw.endswith("\x1e")
    body = json.loads(raw[:-1])
    assert body == {
        "type": 1,
        "target": "SubscribeContractQuotes",
        "arguments": ["CON.F.US.MES.Z26"],
        "invocationId": "1",
    }


def test_quote_gate_stays_flat_without_a_quote_during_the_session():
    assert quote_gate(1, True) == "arm"
    assert quote_gate(0, True) == "flat-open"
    assert quote_gate(0, False) == "flat-closed"


def test_readonly_hub_subscribes_to_quotes_only_and_prints_no_token():
    holder = {}

    def connector(url):
        assert url.startswith("wss://rtc.topstepx.com/hubs/market?access_token=")
        assert TOKEN in url
        holder["socket"] = ScriptedSocket()
        return holder["socket"]

    client = ReadOnlyProjectXClient(
        "user",
        "super-secret-key",
        market_hub_url="https://rtc.topstepx.com/hubs/market",
    )
    client.token = TOKEN
    client._signalr_connector = connector
    quotes = []
    hub = client.connect_quotes("CON.F.US.MES.Z26", lambda *args: quotes.append(args))
    try:
        for _ in range(50):
            if quotes:
                break
            time.sleep(0.02)
        sent = "".join(holder["socket"].sent)
        assert "SubscribeContractQuotes" in sent
        assert "SubscribeContractTrades" not in sent
        assert "Order" not in sent
        assert quotes[0][0] == "CON.F.US.MES.Z26"
        assert quotes[0][1]["lastPrice"] == 5800.25
        assert hub.public_url == "wss://rtc.topstepx.com/hubs/market"
        assert TOKEN not in hub.public_url
    finally:
        hub.stop()
        assert holder["socket"].closed is True


def test_quote_stream_reconnects_after_the_hub_closes():
    sockets = []

    class ClosingSocket(ScriptedSocket):
        def __init__(self, close_after: bool):
            super().__init__()
            self.close_after = close_after

        async def send(self, data):
            await super().send(data)
            if self.close_after and "SubscribeContractQuotes" in data:
                await self.queue.put('{"type":7,"error":"closed"}\x1e')

    def connector(url):
        assert TOKEN in url
        socket = ClosingSocket(close_after=len(sockets) == 0)
        sockets.append(socket)
        return socket

    quotes = []
    notes = []
    hub = JsonSignalRClient(
        "https://rtc.topstepx.com/hubs/market",
        TOKEN,
        {"GatewayQuote": lambda *args: quotes.append(args)},
        [("SubscribeContractQuotes", ["CON.F.US.MES.Z26"])],
        connector=connector,
        on_status=notes.append,
        reconnect_seconds=0.05,
    )
    hub.start()
    try:
        deadline = time.time() + 3
        while time.time() < deadline and len(quotes) < 2:
            time.sleep(0.02)
        assert len(sockets) >= 2
        assert len(quotes) >= 2
        assert any("Reconnecting" in note for note in notes)
        assert any("back" in note for note in notes)
        assert TOKEN not in " ".join(notes)
    finally:
        hub.stop()

"""Status file for the local command center.

The trading loop writes ``logs/state.json`` and ``logs/events.json``.
The dashboard reads those files. It does not call ProjectX.

Nothing in this module places, changes, or cancels an order. The $1,000
figure is the Standard 50K Combine daily loss limit shown on the screen.
The strategy does not read it. The bot's own daily stop stays in config.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from topstepbot.config import BotConfig
from topstepbot.redact import redact, redact_text
from topstepbot.session import cme_equity_index_open, phase_at
from topstepbot.timeutil import CHICAGO, as_chicago

# Display only. Not an order parameter.
TOPSTEP_DAILY_LOSS_LIMIT = 1000.0
QUOTE_STALE_SECONDS = 30
STALE_BOT_SECONDS = 45
EVENT_LIMIT = 200


def console_text(message: str, level: str = "INFO") -> str:
    """One plain sentence for the terminal. The log file keeps the raw line."""
    kind, fields = split_message(message)
    sentence = _sentence(kind, fields, message)
    if level.upper() == "ERROR" and not sentence.lower().startswith("problem"):
        return f"Problem: {sentence}"
    return sentence


_KINDS = {"SIGNAL", "SKIP", "ORDER", "FILL", "PNL", "KILL", "PLAN"}


def split_message(message: str) -> tuple[str, dict]:
    """Split ``KIND key=value`` lines. Values may contain spaces."""
    parts = str(message).split()
    if not parts or parts[0] not in _KINDS:
        return "INFO", {"message": message}
    fields: dict[str, str] = {}
    key: str | None = None
    chunks: list[str] = []
    for part in parts[1:]:
        name, sep, value = part.partition("=")
        if sep and name.replace("_", "").isalnum():
            if key is not None:
                fields[key] = " ".join(chunks).strip()
            key = name
            chunks = [value]
        elif key is None:
            return "INFO", {"message": message}
        else:
            chunks.append(part)
    if key is not None:
        fields[key] = " ".join(chunks).strip()
    if not fields:
        return "INFO", {"message": message}
    return parts[0], fields


def quote_price(args) -> float | None:
    """Last price from a GatewayQuote callback. Never raises."""
    try:
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            args = tuple(args[0])
        data = None
        if len(args) >= 2 and isinstance(args[1], dict):
            data = args[1]
        elif len(args) == 1 and isinstance(args[0], dict):
            raw = args[0]
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if not isinstance(data, dict):
            return None
        for key in ("lastPrice", "last", "price"):
            if data.get(key) is not None:
                return float(data[key])
        bid = data.get("bestBid")
        ask = data.get("bestAsk")
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
    except (TypeError, ValueError):
        return None
    return None


def write_json(path: Path, payload, secrets: list[str] | None = None) -> None:
    """Atomic replace so the dashboard never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = redact(payload, secrets or [])
    text = json.dumps(cleaned, indent=2)
    text = redact_text(text, secrets or [])
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def append_event(path: Path, event: dict, secrets: list[str] | None = None, limit: int = EVENT_LIMIT) -> None:
    current: list = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                current = loaded
        except (OSError, ValueError):
            current = []
    current.append(redact(event, secrets or []))
    write_json(path, current[-limit:], secrets)


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def is_stale(state: dict | None, now: datetime, seconds: float = STALE_BOT_SECONDS) -> bool:
    if not state or not state.get("updated_at"):
        return True
    try:
        updated = datetime.fromisoformat(str(state["updated_at"]))
    except ValueError:
        return True
    if updated.tzinfo is None:
        return True
    return (now - updated).total_seconds() > seconds


class SessionPublisher:
    """Writes the snapshot the dashboard reads. Observation only."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.log_dir = Path(config.runtime.log_dir)
        self.state_path = self.log_dir / "state.json"
        self.events_path = self.log_dir / "events.json"
        self.kill_path = Path(config.runtime.kill_switch_file)
        self.secrets: list[str] = []
        self.account_name = ""
        self.contract_name = ""
        self.balance: float | None = None
        self.quote_price: float | None = None
        self.quote_at: datetime | None = None
        self.mode = "starting"
        self.last_skip = ""
        self._last_write = 0.0

    def attach(self, journal) -> None:
        journal.sink = self.record

    def note_quote(self, price: float, when: datetime) -> None:
        self.quote_price = float(price)
        self.quote_at = when

    def record(self, level: str, message: str) -> None:
        # Historical warmup already happened. Don't fill the feed with old days.
        if self.mode in {"starting", "warming"}:
            return
        kind, fields = split_message(message)
        if kind == "SKIP":
            self.last_skip = fields.get("reason", "")
        when = _event_time(fields)
        text = redact_text(console_text(message, level), self.secrets)
        append_event(
            self.events_path,
            {"time": when, "level": level.lower(), "kind": kind.lower(), "text": text},
            self.secrets,
        )

    def publish(self, engine, broker, now: datetime, *, force: bool = False) -> None:
        if not force and (time.monotonic() - self._last_write) < 1.0:
            return
        if self.mode in {"live", "connecting"} and self.kill_path.exists():
            self.mode = "killed"
        state = build_state(self, engine, broker, now)
        write_json(self.state_path, state, self.secrets)
        self._last_write = time.monotonic()


def build_state(publisher: SessionPublisher, engine, broker, now: datetime) -> dict:
    config = publisher.config
    local = as_chicago(now, config.session.timezone)
    phase = phase_at(now, config.session)
    market_open = cme_equity_index_open(now)
    opening = _opening(engine)
    price, source = _price(publisher, broker)
    quote_at = publisher.quote_at.astimezone(CHICAGO).isoformat() if publisher.quote_at else None
    quote_age = None
    if publisher.quote_at is not None:
        quote_age = max(0.0, (now - publisher.quote_at).total_seconds())
    quote_stale = source != "quote" or quote_age is None or quote_age > QUOTE_STALE_SECONDS
    position = _position(engine, broker, price)
    in_trade = position is not None
    pending = _active_pending(engine)
    halted = bool(getattr(getattr(engine, "day", None), "halted", False))
    halt_reason = str(getattr(getattr(engine, "day", None), "halt_reason", "") or "")
    day_flattened = bool(getattr(engine, "day_flattened", False))
    armed = bool(getattr(engine, "armed", False))
    status = derive_status(
        mode=publisher.mode,
        armed=armed,
        in_trade=in_trade,
        day_flattened=day_flattened,
        halted=halted,
        halt_reason=halt_reason,
        pending=bool(pending),
        quote_stale=quote_stale,
        market_open=market_open,
        phase=phase.value,
    )
    realized = float(getattr(getattr(engine, "day", None), "realized", 0.0) or 0.0)
    unrealized = float(position["unrealized"]) if position is not None else 0.0
    if engine is not None and position is None:
        unrealized = float(getattr(engine.day, "unrealized", 0.0) or 0.0)
    mtm = realized + unrealized
    loss_used = min(0.0, mtm)
    label, target = _countdown_target(local, config.session)
    equity = _equity(engine, config, mtm)
    mll_floor = float(getattr(engine, "mll_floor", config.account.starting_balance - config.risk.topstep_max_loss))
    headline = _headline(
        status=status,
        pending=pending,
        position=position,
        opening=opening,
        phase=phase.value,
        armed=armed,
        last_skip=publisher.last_skip,
        mode=publisher.mode,
    )
    return {
        "updated_at": local.isoformat(),
        "status": status,
        "headline": headline,
        "account_name": publisher.account_name,
        "contract_name": publisher.contract_name,
        "countdown_label": label,
        "countdown_target": target.isoformat(),
        "price": price,
        "price_source": source,
        "quote_at": quote_at,
        "quote_stale_seconds": QUOTE_STALE_SECONDS,
        "price_relation": _relation(price, opening),
        "opening_range": opening,
        "position": position,
        "realized": round(realized, 2),
        "unrealized": round(unrealized, 2),
        "pnl": round(mtm, 2),
        "trades_today": int(getattr(getattr(engine, "day", None), "trades", 0) or 0),
        "max_trades": int(config.risk.max_trades_per_day),
        "bot_stop": float(config.risk.daily_max_loss),
        "bot_stop_remaining": round(max(0.0, config.risk.daily_max_loss + loss_used), 2),
        "topstep_daily_loss": TOPSTEP_DAILY_LOSS_LIMIT,
        "topstep_daily_remaining": round(max(0.0, TOPSTEP_DAILY_LOSS_LIMIT + loss_used), 2),
        "topstep_max_loss": float(config.risk.topstep_max_loss),
        "max_loss_remaining": round(max(0.0, equity - mll_floor), 2),
        "balance": publisher.balance,
        "bars": _bars(engine),
        "armed": armed,
        "mode": publisher.mode,
    }


def derive_status(
    *,
    mode: str,
    armed: bool,
    in_trade: bool,
    day_flattened: bool,
    halted: bool,
    halt_reason: str,
    pending: bool,
    quote_stale: bool,
    market_open: bool,
    phase: str,
) -> str:
    if mode == "killed":
        return "KILLED"
    if mode == "checking":
        return "CHECKING"
    if mode == "halted" or (halted and halt_reason not in {"", "session flatten"}):
        return "HALTED"
    if mode in {"warming", "starting"}:
        return "WARMING"
    if day_flattened or phase == "flat" or halt_reason == "session flatten":
        return "FLAT"
    if in_trade:
        return "IN TRADE"
    if mode == "disconnected" or (mode == "live" and quote_stale and market_open):
        return "DISCONNECTED"
    if pending:
        return "WATCHING"
    if armed:
        return "ARMED"
    return "WATCHING"


def write_check_state(directory: Path, config: BotConfig, steps: dict, secrets: list[str]) -> None:
    """One snapshot at the end of the read-only check. No extra API call."""
    accounts = ((steps.get("accounts") or {}).get("response") or {}).get("accounts") or []
    name = ""
    balance = None
    for raw in accounts:
        if not isinstance(raw, dict):
            continue
        account_name = str(raw.get("name") or "")
        if account_name.upper().startswith("50KTC"):
            name = account_name
            if raw.get("balance") is not None:
                balance = float(raw["balance"])
            break
    contract_name = ""
    contracts = ((steps.get("contracts") or {}).get("response") or {}).get("contracts") or []
    for raw in contracts:
        if isinstance(raw, dict) and raw.get("activeContract") and "MES" in str(raw.get("name") or "").upper():
            contract_name = str(raw.get("name") or "")
            break
    sample = (steps.get("signalr") or {}).get("sample") or {}
    data = sample.get("data") if isinstance(sample, dict) else None
    price = None
    if isinstance(data, dict) and data.get("lastPrice") is not None:
        try:
            price = float(data["lastPrice"])
        except (TypeError, ValueError):
            price = None
    now = datetime.now(CHICAGO)
    publisher = SessionPublisher(config)
    publisher.log_dir = directory
    publisher.state_path = directory / "state.json"
    publisher.events_path = directory / "events.json"
    publisher.mode = "checking"
    publisher.account_name = name
    publisher.contract_name = contract_name
    publisher.balance = balance
    publisher.secrets = list(secrets)
    if price is not None:
        publisher.note_quote(price, now)
    # A stand-in so the check screen is obviously not a live armed bot.
    write_json(publisher.state_path, build_state(publisher, None, None, now), secrets)


def _sentence(kind: str, fields: dict, message: str) -> str:
    if kind == "SIGNAL" and fields.get("state") == "break_waiting_retest":
        price = _num(fields.get("price"))
        shown = f"{price:.2f}" if price is not None else fields.get("level", "the level")
        if fields.get("side") == "short":
            return f"Price broke below {shown} — waiting for a bounce to retest."
        return f"Price broke above {shown} — waiting for a pullback to retest."
    if kind == "SIGNAL" and fields.get("state") == "signal":
        side = "Long" if fields.get("side") == "long" else "Short"
        return (
            f"{side} signal at {fields.get('level', 'a level')}. "
            f"Entry {fields.get('entry')}, stop {fields.get('stop')}, target {fields.get('target')}."
        )
    if kind == "SKIP":
        return _skip_sentence(fields.get("reason", ""))
    if kind == "ORDER" and fields.get("action") == "flatten":
        return f"Flattening the position ({_pretty_reason(fields.get('reason', 'flatten'))})."
    if kind == "ORDER" and fields.get("action") == "cancel_entry":
        return "Cancelled the working entry. It was not filled."
    if kind == "ORDER" and fields.get("action") == "modify_stop":
        return f"Stop moved to {fields.get('stop')}."
    if kind == "ORDER" and fields.get("qty"):
        verb = "Buy" if fields.get("side") == "long" else "Sell"
        return (
            f"Order sent: {verb} {fields.get('qty')}. "
            f"Entry {fields.get('entry')}, stop {fields.get('stop')}, target {fields.get('target')}."
        )
    if kind == "FILL":
        return (
            f"Filled {fields.get('side', '')} {fields.get('qty', '')} "
            f"at {fields.get('price', '')} ({fields.get('role', 'fill')})."
        ).replace("  ", " ")
    if kind == "PNL":
        pnl = _num(fields.get("pnl"))
        if pnl is None:
            return "Trade closed."
        word = "gain" if pnl >= 0 else "loss"
        reason = _pretty_reason(fields.get("reason", ""))
        return f"Trade closed. {word.capitalize()} of ${abs(pnl):,.2f}. {reason}."
    if kind == "KILL":
        return "Kill switch is on. Flattening and stopping."
    if kind == "PLAN" and "OR_high" in fields:
        return f"Opening range set. High {fields.get('OR_high')}, low {fields.get('OR_low')}."
    if message.startswith("PLAN above") or message.startswith("PLAN below"):
        return "Plan for the morning is on the chart: high for a long, low for a short."
    return message


def _skip_sentence(reason: str) -> str:
    text = reason.strip()
    if text.startswith("fakeout"):
        return "Skipped: fakeout."
    if text.startswith("retest timeout"):
        return "Skipped: the pullback did not arrive in time."
    if text.startswith("cooldown"):
        return "Skipped: that direction is on cooldown after a stop."
    known = {
        "bot is not armed": "Skipped: the bot is not armed, so no order was sent.",
        "chop filter": "Skipped: the market looks choppy.",
        "no chase": "Skipped: price already ran too far.",
        "indicators warming up": "Skipped: indicators are still warming up.",
        "minimum order interval": "Skipped: waiting for the minimum time between orders.",
        "stop too wide for the risk budget": "Skipped: the stop is too far for the risk on this trade.",
        "no key level at least 2R away": "Skipped: no target far enough away.",
        "stop is not beyond the entry": "Skipped: the stop was not beyond the entry.",
        "index agreement feed is not connected": "Skipped: the index filter has no data.",
    }
    if text in known:
        return known[text]
    if text:
        return f"Skipped: {_pretty_reason(text)}."
    return "Skipped."


def _pretty_reason(reason: str) -> str:
    text = reason.replace("_", " ").strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _headline(*, status, pending, position, opening, phase, armed, last_skip, mode) -> str:
    if status == "KILLED":
        return "Kill switch is on. The bot is flattening and stopping."
    if status == "HALTED":
        return "Trading stopped for today. No new orders."
    if status == "WARMING":
        return "Loading the last few days of prices. Not trading yet."
    if status == "CHECKING":
        return "Read-only connection check. This screen cannot place orders."
    if status == "DISCONNECTED":
        return "No fresh MES quote. Staying flat until prices come back."
    if status == "FLAT":
        return "Flat for the day. Done until the next session."
    if position is not None:
        target = f", target {position['target']:.2f}" if position.get("target") is not None else ""
        stop = f"{position['stop']:.2f}" if position.get("stop") is not None else "the stop"
        return (
            f"{position['side']} {position['contracts']} from {position['entry']:.2f}. "
            f"Stop {stop}{target}."
        )
    if pending:
        item = pending[0]
        price = f"{float(item['level']):.2f}"
        if item["side"] == "short":
            return f"Price broke below {price} — waiting for a bounce to retest."
        return f"Price broke above {price} — waiting for a pullback to retest."
    if last_skip and mode == "live":
        return _skip_sentence(last_skip)
    if opening is None:
        return "Waiting for the 8:30–8:45 CT opening range."
    if phase == "opening_range":
        return "Building the opening range. No new trades until 8:45 CT."
    if phase == "entry" and armed:
        return f"Watching for a break of {opening['high']:.2f} or {opening['low']:.2f}."
    if phase == "entry" and not armed:
        return "Staying flat. New orders are off."
    if phase == "manage":
        return "The entry window is over. Managing until the 10:30 CT flatten."
    if phase == "before":
        return "Waiting for the 8:30 CT open."
    return "Watching the session."


def _opening(engine) -> dict | None:
    opening = getattr(engine, "opening", None)
    if opening is None:
        return None
    return {"high": float(opening.high), "low": float(opening.low)}


def _price(publisher: SessionPublisher, broker) -> tuple[float | None, str]:
    if publisher.quote_price is not None:
        return float(publisher.quote_price), "quote"
    last = getattr(broker, "last_price", None)
    if last is not None:
        return float(last), "bar"
    return None, "none"


def _active_pending(engine) -> list[dict]:
    rows = []
    for item in getattr(engine, "pending", []) or []:
        if getattr(item, "done", False):
            continue
        side = getattr(getattr(item, "side", None), "value", None) or str(getattr(item, "side", ""))
        rows.append({"side": side, "level": float(item.level), "name": str(getattr(item, "level_name", ""))})
    return rows


def _position(engine, broker, price: float | None) -> dict | None:
    if engine is None or broker is None:
        return None
    trade = getattr(engine, "trade", None)
    qty = int(broker.net_qty() or 0)
    filled = int(getattr(trade, "filled_qty", 0) or 0)
    if qty == 0 and filled == 0:
        return None
    contracts = abs(qty) if qty else filled
    if trade is not None and filled:
        side = "Long" if getattr(trade.side, "value", "") == "long" else "Short"
        entry = float(trade.entry_notional) / filled
    else:
        side = "Long" if qty > 0 else "Short"
        entry = broker.average_price()
        if entry is None:
            return None
        entry = float(entry)
    stop = getattr(trade, "initial_stop", None) if trade is not None else None
    target = None
    groups = getattr(broker, "_groups", {}) or {}
    group_ids = list(getattr(trade, "groups", []) or []) or list(groups.keys())
    for group_id in group_ids:
        group = groups.get(group_id)
        if group is None:
            continue
        group_stop = group.get("stop_price") if isinstance(group, dict) else getattr(group, "stop_price", None)
        group_target = group.get("target_price") if isinstance(group, dict) else getattr(group, "target_price", None)
        if group_stop is not None:
            stop = float(group_stop)
        if target is None and group_target is not None:
            target = float(group_target)
    unrealized = float(broker.unrealized(price if price is not None else entry))
    return {
        "side": side,
        "contracts": contracts,
        "entry": round(float(entry), 2),
        "stop": None if stop is None else round(float(stop), 2),
        "target": None if target is None else round(float(target), 2),
        "unrealized": round(unrealized, 2),
    }


def _bars(engine, limit: int = 120) -> list[dict]:
    if engine is None or not getattr(engine, "minutes", None) or getattr(engine, "session_day", None) is None:
        return []
    session = engine.config.session
    chosen = []
    for bar in engine.minutes:
        local = as_chicago(bar.time, session.timezone)
        if local.date() != engine.session_day:
            continue
        if local.time() < session.rth_open or local.time() > session.flatten_time:
            continue
        chosen.append(bar)
    rows = []
    for bar in chosen[-limit:]:
        rows.append(
            {
                "t": bar.time.astimezone(CHICAGO).strftime("%H:%M"),
                "o": bar.open,
                "h": bar.high,
                "l": bar.low,
                "c": bar.close,
            }
        )
    return rows


def _equity(engine, config: BotConfig, mtm: float) -> float:
    if engine is None:
        balance = config.account.starting_balance
        return float(balance)
    prior = float(getattr(engine, "cumulative_prior", 0.0) or 0.0)
    return float(config.account.starting_balance) + prior + mtm


def _countdown_target(local: datetime, session) -> tuple[str, datetime]:
    open_at = datetime.combine(local.date(), session.rth_open, tzinfo=local.tzinfo)
    flat_at = datetime.combine(local.date(), session.flatten_time, tzinfo=local.tzinfo)
    if local.timetz().replace(tzinfo=None) >= session.vwap_anchor:
        nxt = local.date() + timedelta(days=1)
        open_at = datetime.combine(nxt, session.rth_open, tzinfo=local.tzinfo)
        return "Session starts in", open_at
    if local.timetz().replace(tzinfo=None) < session.rth_open:
        return "Session starts in", open_at
    if local.timetz().replace(tzinfo=None) >= session.flatten_time:
        return "Flat since 10:30 CT", flat_at
    return "Flat at 10:30 CT in", flat_at


def _relation(price: float | None, opening: dict | None) -> str:
    if price is None or opening is None:
        return ""
    high = float(opening["high"])
    low = float(opening["low"])
    if low <= price <= high:
        return "Price is inside the opening range."
    if price > high:
        return f"Price is {price - high:.2f} points above the opening range high."
    return f"Price is {low - price:.2f} points below the opening range low."


def _event_time(fields: dict) -> str:
    raw = fields.get("bar") or fields.get("time")
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return parsed.astimezone(CHICAGO).isoformat()
        except ValueError:
            pass
    return datetime.now(CHICAGO).isoformat()


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

"""Supervised session loop. This process places orders only while you run it.

It is not a server. Do not run it on a VPS, through a VPN, or on a remote machine.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from topstepbot.bars import load_bars
from topstepbot.broker.accounts import choose_account
from topstepbot.broker.paper import PaperBroker
from topstepbot.broker.projectx import ProjectXBroker, ProjectXClient, ProjectXError, pick_front_month
from topstepbot.broker.signalr import public_hub_url, quote_gate
from topstepbot.config import BotConfig
from topstepbot.journal import Journal
from topstepbot.killswitch import KillSwitch
from topstepbot.session import cme_equity_index_open
from topstepbot.strategy.engine import StrategyEngine
from topstepbot.timeutil import CHICAGO


def run_paper(config: BotConfig, csv_path: str | Path, speed: float, armed: bool) -> None:
    bars = load_bars(csv_path, config.session.timezone)
    journal = Journal(config.runtime.log_dir, clock=bars[0].time if bars else None)
    kill = KillSwitch(config.runtime.kill_switch_file)
    kill.clear_file()
    journal.info("Paper mode. No orders go to Topstep. Type 'kill' and press Enter, or create the KILL file.")
    broker = PaperBroker(config)
    engine = StrategyEngine(config, broker, journal, armed=armed)
    watcher = _keyboard_watcher(kill)
    try:
        for bar in bars:
            if kill.execute(broker, bar.close, bar.time, journal):
                journal.info("Kill switch stopped paper mode.")
                break
            engine.on_minute(bar)
            if speed > 0:
                time.sleep(speed)
        else:
            engine.finish()
    except KeyboardInterrupt:
        kill.trip("Ctrl+C")
        price = broker.last_price or (bars[-1].close if bars else 0)
        when = bars[-1].time if bars else datetime.now(CHICAGO)
        kill.execute(broker, price, when, journal)
    finally:
        watcher.join(timeout=0.2)


def run_practice(config: BotConfig, armed: bool) -> None:
    """Connect to ProjectX and trade the configured simulated account.

    This function sends real API orders when it is armed. The caller must be
    the person at the PC. This repository's tests never call it.
    """
    if not armed:
        raise SystemExit(
            "Practice mode will not start until you arm it. "
            "Sit down at this PC, then run: python -m topstepbot practice --arm"
        )
    load_dotenv()
    username = os.environ.get("PROJECTX_USERNAME", "").strip()
    api_key = os.environ.get("PROJECTX_API_KEY", "").strip()
    if not username or not api_key:
        raise SystemExit(
            "Missing PROJECTX_USERNAME or PROJECTX_API_KEY. Copy .env.example to .env and fill them in. "
            "The bot will not print those values."
        )
    client = ProjectXClient(
        username=username,
        api_key=api_key,
        api_url=os.environ.get("PROJECTX_API_URL", "https://api.topstepx.com"),
        user_hub_url=os.environ.get("PROJECTX_USER_HUB_URL", "https://rtc.topstepx.com/hubs/user"),
        market_hub_url=os.environ.get("PROJECTX_MARKET_HUB_URL", "https://rtc.topstepx.com/hubs/market"),
        max_consecutive_failures=config.runtime.api_max_consecutive_failures,
        failure_window_seconds=config.runtime.api_failure_window_seconds,
    )
    journal = Journal(config.runtime.log_dir)
    journal.info(
        "Practice mode. You are responsible for every order. Stay at this PC until 10:30 CT. "
        "Type 'kill' and press Enter, or create a file named KILL, to flatten."
    )
    if config.exits.move_stop_to_breakeven_after_first_target:
        journal.info(
            "Breakeven-after-target is ON in config. Topstep prohibits using auto-breakeven "
            "to exploit sim fills. Leave it on only if you decided that yourself."
        )
    try:
        client.login()
        accounts = client.search_accounts(True)
        account = choose_account(accounts, config)
        journal.info(f"Account {account.id} {account.name} selected.")
        contract = _resolve_contract(client, config)
        journal.info(
            f"Contract {contract.get('id')} {contract.get('name')} {contract.get('description')}"
        )
    except ProjectXError as exc:
        journal.error(str(exc))
        raise SystemExit(str(exc)) from exc

    broker = ProjectXBroker(config, client, account, contract)
    kill = KillSwitch(config.runtime.kill_switch_file)
    kill.clear_file()
    hub = None
    watcher = None
    try:
        try:
            _reconcile_startup(broker, journal, config.runtime.flatten_on_start)
            # Replay does not search trades, flatten, or place. The contract and
            # account were resolved once above and are not looked up again.
            broker.orders_enabled = False
            history = _warmup_bars(client, contract["id"], config)
            journal.info(f"Loaded {len(history)} historical 1-minute bars for warmup.")
            engine = StrategyEngine(config, broker, journal, armed=False)
            for bar in history:
                engine.on_minute(bar)
            broker.orders_enabled = True
            hub, quote_count = _await_live_quotes(
                client, contract["id"], journal, config.runtime.quote_timeout_seconds
            )
            decision = quote_gate(quote_count, cme_equity_index_open(datetime.now(CHICAGO)))
            if decision == "arm":
                engine.armed = True
                journal.info("Live MES quote received. Entries are allowed inside the session window.")
            elif decision == "flat-open":
                engine.armed = False
                journal.error(
                    "No live MES quotes within "
                    f"{config.runtime.quote_timeout_seconds:g}s while the equity-index session is open. "
                    "Staying flat. No new orders."
                )
            else:
                engine.armed = False
                journal.info("MES session is closed and no quote arrived. Staying flat.")
            watcher = _keyboard_watcher(kill)
            seen = {bar.time for bar in history}
            while True:
                now = datetime.now(CHICAGO)
                price = broker.last_price or (history[-1].close if history else 0)
                if kill.execute(broker, price, now, journal):
                    journal.info("Kill switch flattened the account. Bot stopped.")
                    break
                action = _poll_once(client, broker, engine, contract["id"], config, seen, now, journal)
                if action == "flat":
                    break
                time.sleep(config.runtime.poll_seconds)
        except ProjectXError as exc:
            _stop_for_api_failure(broker, journal, exc)
    except KeyboardInterrupt:
        now = datetime.now(CHICAGO)
        price = broker.last_price or 0
        broker.orders_enabled = True
        kill.trip("Ctrl+C")
        try:
            kill.execute(broker, price, now, journal)
            journal.info("Ctrl+C flattened the account.")
        except ProjectXError as exc:
            _stop_for_api_failure(broker, journal, exc)
    finally:
        if hub is not None:
            stop = getattr(hub, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        if watcher is not None:
            watcher.join(timeout=0.2)


def _resolve_contract(client: ProjectXClient, config: BotConfig) -> dict:
    live = config.broker.projectx_use_live_data
    if config.instrument.contract_id:
        contracts = client.search_contracts(config.instrument.symbol, live=live)
        for contract in contracts:
            if contract.get("id") == config.instrument.contract_id:
                return contract
        # The pinned id may still be valid if search did not return it.
        # TODO-VERIFY: Contract/searchById was not in the pages fetched for this client.
        return {
            "id": config.instrument.contract_id,
            "name": config.instrument.symbol,
            "description": "pinned by config",
            "tickSize": config.instrument.tick_size,
            "tickValue": config.instrument.tick_value,
            "activeContract": True,
        }
    contracts = client.available_contracts(live=live)
    if not contracts:
        contracts = client.search_contracts(config.instrument.symbol, live=live)
    return pick_front_month(contracts, config.instrument.symbol)


def _warmup_bars(client: ProjectXClient, contract_id: str, config: BotConfig) -> list:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=4)
    # Page in one-day slices so a single call stays under the 20,000 bar cap.
    cursor = start
    collected = []
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=1), end)
        collected.extend(
            client.retrieve_bars(
                contract_id,
                cursor,
                chunk_end,
                live=config.broker.projectx_use_live_data,
            )
        )
        cursor = chunk_end
    collected.sort(key=lambda bar: bar.time)
    deduped = []
    seen = set()
    for bar in collected:
        if bar.time in seen:
            continue
        seen.add(bar.time)
        deduped.append(bar)
    return deduped


def _poll_once(client, broker, engine, contract_id: str, config: BotConfig, seen: set, now: datetime, journal: Journal) -> str:
    """One live cycle: one bar fetch, one trade search, then any new minutes.

    Trade/search is not inside the per-bar loop. API failures propagate so the
    caller can stop. This function does not flatten just because a request failed.
    """
    fresh = client.retrieve_bars(
        contract_id,
        now - timedelta(minutes=30),
        now,
        live=config.broker.projectx_use_live_data,
        include_partial=False,
    )
    price = broker.last_price or (fresh[-1].close if fresh else 0)
    engine.service(now, price)
    new_bars = [bar for bar in fresh if bar.time not in seen and bar.time + timedelta(minutes=1) <= now]
    for bar in new_bars:
        seen.add(bar.time)
        engine.on_minute(bar)
    if engine.day_flattened and engine.day.halt_reason == "session flatten":
        journal.info("Session is flat. Bot is done for the day.")
        return "flat"
    return "continue"


def _reconcile_startup(broker: ProjectXBroker, journal: Journal, flatten_on_start: bool) -> None:
    """Print the open book once. Flatten once when config says to and something is open."""
    positions = broker.client.search_open_positions(broker.account.id)
    orders = broker.client.search_open_orders(broker.account.id)
    _log_book(journal, positions, orders)
    if not flatten_on_start:
        return
    open_positions = [
        item
        for item in positions
        if str(item.get("contractId")) == broker.contract_id and int(item.get("size") or 0) > 0
    ]
    working = [item for item in orders if str(item.get("contractId")) == broker.contract_id]
    if not open_positions and not working:
        return
    journal.info("Open position or working order found at startup. Flattening once before new entries.")
    price = broker.last_price
    if price is None and open_positions:
        price = float(open_positions[0].get("averagePrice") or 0)
    broker.flatten(price or 0, datetime.now(CHICAGO), reason="startup")


def _stop_for_api_failure(broker: ProjectXBroker, journal: Journal, exc: BaseException) -> None:
    """One message, one book check, then idle. No flatten retry and no spin."""
    broker.orders_enabled = False
    fold = getattr(broker, "_errors", None)
    if fold is not None:
        fold.flush()
    journal.error(
        "API errors persisted. Trading stopped. "
        f"{exc} "
        "No more orders will be sent."
    )
    _report_book_once(broker, journal)


def _report_book_once(broker: ProjectXBroker, journal: Journal) -> None:
    allow = getattr(broker.client, "allow_halt_probe", None)
    if callable(allow):
        allow(2)
    try:
        positions = broker.client.search_open_positions(broker.account.id)
    except ProjectXError as exc:
        journal.error(f"Could not list open positions: {exc}")
        positions = None
    except Exception as exc:
        journal.error(f"Could not list open positions: {type(exc).__name__}")
        positions = None
    try:
        orders = broker.client.search_open_orders(broker.account.id)
    except ProjectXError as exc:
        journal.error(f"Could not list open orders: {exc}")
        orders = None
    except Exception as exc:
        journal.error(f"Could not list open orders: {type(exc).__name__}")
        orders = None
    _log_book(journal, positions, orders)


def _log_book(journal: Journal, positions: list | None, orders: list | None) -> None:
    if positions is None:
        pass
    elif not positions:
        journal.info("No open positions.")
    else:
        for position in positions:
            journal.info(
                "Open position "
                f"contract={position.get('contractId')} size={position.get('size')} "
                f"average={position.get('averagePrice')}"
            )
    if orders is None:
        pass
    elif not orders:
        journal.info("No working orders.")
    else:
        for order in orders:
            journal.info(
                "Working order "
                f"id={order.get('id')} contract={order.get('contractId')} "
                f"type={order.get('type')} side={order.get('side')} size={order.get('size')}"
            )


def _await_live_quotes(client: ProjectXClient, contract_id: str, journal: Journal, timeout: float):
    """Connect to the market hub and wait until one quote arrives, or the timeout.

    Returns ``(hub, quote_count)``. The hub object is None when the handshake
    fails. Exception text is not logged: it can contain the tokenized hub URL.
    """
    quotes: list = []

    def on_quote(*_args) -> None:
        quotes.append(1)

    def on_trade(*_args) -> None:
        return None

    try:
        shown = public_hub_url(client.market_hub_url)
    except ProjectXError:
        shown = "market hub"
    journal.info(f"Connecting to {shown}. The access token is not logged.")
    try:
        hub = client.connect_market_hub(contract_id, on_trade, on_quote)
    except Exception as exc:
        journal.error(f"Market hub failed ({type(exc).__name__}). Staying flat.")
        return None, 0
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline and not quotes:
        if getattr(hub, "error", None) is not None:
            journal.error(f"Market hub closed ({type(hub.error).__name__}).")
            break
        time.sleep(0.2)
    journal.info(f"Quotes received during the wait: {len(quotes)}.")
    return hub, len(quotes)


def _keyboard_watcher(kill: KillSwitch) -> threading.Thread:
    def watch() -> None:
        try:
            while not kill.tripped:
                line = input()
                if line.strip().lower() in {"kill", "flat", "stop", "q"}:
                    kill.trip(f"typed {line.strip().lower()}")
                    return
        except EOFError:
            return
        except Exception:
            return

    thread = threading.Thread(target=watch, name="kill-keyboard", daemon=True)
    thread.start()
    return thread

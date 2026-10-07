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
from topstepbot.config import BotConfig
from topstepbot.journal import Journal
from topstepbot.killswitch import KillSwitch
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
    if config.runtime.flatten_on_start:
        _flatten_leftovers(broker, journal)
    history = _warmup_bars(client, contract["id"], config)
    journal.info(f"Loaded {len(history)} historical 1-minute bars for warmup.")
    engine = StrategyEngine(config, broker, journal, armed=True)
    for bar in history:
        engine.on_minute(bar)
    kill = KillSwitch(config.runtime.kill_switch_file)
    kill.clear_file()
    _start_market_hub(client, contract["id"], journal)
    watcher = _keyboard_watcher(kill)
    seen = {bar.time for bar in history}
    try:
        while True:
            now = datetime.now(CHICAGO)
            price = broker.last_price or (history[-1].close if history else 0)
            if kill.execute(broker, price, now, journal):
                journal.info("Kill switch flattened the account. Bot stopped.")
                break
            try:
                fresh = client.retrieve_bars(
                    contract["id"],
                    now - timedelta(minutes=30),
                    now,
                    live=config.broker.projectx_use_live_data,
                    include_partial=False,
                )
            except ProjectXError as exc:
                journal.error(f"Bar poll failed: {exc}. Flattening and stopping.")
                kill.trip("data feed unhealthy")
                kill.execute(broker, price, now, journal)
                break
            new_bars = [bar for bar in fresh if bar.time not in seen and bar.time + timedelta(minutes=1) <= now]
            for bar in new_bars:
                seen.add(bar.time)
                engine.on_minute(bar)
                price = bar.close
            engine.service(now, price)
            if engine.day_flattened and engine.day.halt_reason == "session flatten":
                journal.info("Session is flat. Bot is done for the day.")
                break
            time.sleep(config.runtime.poll_seconds)
    except KeyboardInterrupt:
        now = datetime.now(CHICAGO)
        price = broker.last_price or 0
        kill.trip("Ctrl+C")
        kill.execute(broker, price, now, journal)
        journal.info("Ctrl+C flattened the account.")
    finally:
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


def _flatten_leftovers(broker: ProjectXBroker, journal: Journal) -> None:
    now = datetime.now(CHICAGO)
    positions = broker.client.search_open_positions(broker.account.id)
    relevant = [item for item in positions if str(item.get("contractId")) == broker.contract_id and int(item.get("size") or 0) > 0]
    if not relevant:
        return
    journal.info("Open position found at startup. Flattening it before new entries.")
    broker.flatten(broker.last_price or float(relevant[0].get("averagePrice") or 0), now)


def _start_market_hub(client: ProjectXClient, contract_id: str, journal: Journal) -> None:
    def on_trade(_contract, data) -> None:
        # Quotes are advisory. Bars still come from retrieveBars.
        price = data.get("price") if isinstance(data, dict) else None
        if price is not None:
            journal.event("QUOTE", price=price)

    try:
        client.connect_market_hub(contract_id, on_trade)
        journal.info("Market hub connected. TODO-VERIFY: this Python SignalR handshake has not been live-tested.")
    except Exception as exc:
        # Do not log the exception text. SignalR errors can include the hub URL,
        # and that URL carries the session token.
        journal.info(
            f"Market hub not connected ({type(exc).__name__}). Bars will come from retrieveBars polling only."
        )


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

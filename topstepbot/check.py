"""Read-only ProjectX connection check.

`python -m topstepbot check` logs in, lists accounts, looks up MES, and reads
recent 5-minute bars. It can also listen to market quotes for a few seconds.

It never places, modifies, or cancels orders. The client it constructs has
those methods disabled, and its HTTP layer rejects every path except login,
account search, contract search, and retrieveBars.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from topstepbot.broker.projectx import ProjectXError, pick_front_month
from topstepbot.broker.readonly import (
    READ_ONLY_PATHS,
    ReadOnlyProjectXClient,
    accounts_from_body,
)
from topstepbot.broker.signalr import public_hub_url
from topstepbot.config import BotConfig
from topstepbot.models import AccountInfo
from topstepbot.redact import redact, redact_text
from topstepbot.session import cme_equity_index_open

_STEP_ORDER = ("auth", "accounts", "contracts", "bars", "signalr")
_50K_NAME = re.compile(r"(?<![0-9])50K", re.IGNORECASE)


def run_check(
    config: BotConfig,
    *,
    skip_signalr: bool = False,
    signalr_seconds: float = 20,
    session=None,
    username: str | None = None,
    api_key: str | None = None,
    api_url: str | None = None,
    market_hub_url: str | None = None,
    log_dir: str | Path | None = None,
    now: datetime | None = None,
    stdout=None,
    listen_quotes=None,
    sleep=None,
) -> int:
    """Run the check and return 0 when the four REST steps pass.

    A SignalR handshake failure is reported and does not by itself fail the
    command. Auth, accounts, contracts, and bars do.
    """
    out = stdout or sys.stdout
    secrets: list[str] = []

    def say(message: str = "") -> None:
        print(redact_text(message, secrets), file=out)

    steps: dict[str, dict] = {}
    moment = now or datetime.now(timezone.utc)
    directory = Path(log_dir) if log_dir is not None else Path(config.runtime.log_dir)

    say("Topstep connection check (read-only).")
    say("This command never calls an Order endpoint. It cannot place, modify, or cancel orders.")
    say("")

    if username is None or api_key is None:
        load_dotenv()
        if username is None:
            username = os.environ.get("PROJECTX_USERNAME", "").strip()
        if api_key is None:
            api_key = os.environ.get("PROJECTX_API_KEY", "").strip()
        if api_url is None:
            api_url = os.environ.get("PROJECTX_API_URL", "https://api.topstepx.com")
        if market_hub_url is None:
            market_hub_url = os.environ.get("PROJECTX_MARKET_HUB_URL", "https://rtc.topstepx.com/hubs/market")
    api_url = api_url or "https://api.topstepx.com"
    market_hub_url = market_hub_url or "https://rtc.topstepx.com/hubs/market"
    if api_key:
        secrets.append(api_key)

    say("1. Auth  POST /api/Auth/loginKey")
    if not username or not api_key:
        say("   FAIL  Missing PROJECTX_USERNAME or PROJECTX_API_KEY in .env. The values are not shown.")
        steps["auth"] = {"status": "FAIL", "summary": "missing .env credentials"}
        _skip_after(steps, "auth")
        return _finish(directory, moment, steps, secrets, say)

    client = ReadOnlyProjectXClient(
        username=username,
        api_key=api_key,
        api_url=api_url,
        market_hub_url=market_hub_url,
        session=session,
    )
    try:
        login_body = client.login()
    except ProjectXError as exc:
        say(f"   FAIL  {exc}")
        steps["auth"] = {"status": "FAIL", "summary": "login failed", "error": str(exc)}
        _skip_after(steps, "auth")
        return _finish(directory, moment, steps, secrets, say)
    if client.token:
        secrets.append(client.token)
    say("   PASS  Logged in. A token was received and is not shown.")
    steps["auth"] = {
        "status": "PASS",
        "summary": "logged in",
        "response": login_body,
    }

    say("")
    say("2. Accounts  POST /api/Account/search  {onlyActiveAccounts: true}")
    try:
        account_body = client.search_accounts_raw(True)
    except ProjectXError as exc:
        say(f"   FAIL  {exc}")
        steps["accounts"] = {"status": "FAIL", "summary": "account search failed", "error": str(exc)}
        account_body = None
    if account_body is not None:
        accounts = accounts_from_body(account_body)
        if not accounts:
            say("   No active accounts were returned.")
            steps["accounts"] = {
                "status": "FAIL",
                "summary": "no active accounts",
                "request": {"onlyActiveAccounts": True},
                "response": account_body,
            }
        else:
            for account in accounts:
                say(f"   {_format_account(account)}")
            combines = identify_50k_combines(accounts)
            if len(combines) == 1:
                chosen = combines[0]
                say(f"   50K Combine: id={chosen.id} name={chosen.name}")
                summary = f"50K Combine id={chosen.id} name={chosen.name}"
            elif len(combines) > 1:
                listed = ", ".join(f"{item.id}:{item.name}" for item in combines)
                say(f"   More than one account looks like a 50K Combine: {listed}")
                summary = "multiple 50K Combine matches"
            else:
                say(
                    "   No account name looked like a 50K Combine "
                    "(expected a name containing 50K and TC or COMBINE)."
                )
                summary = "50K Combine not identified by name"
            needle = config.account.account_name_contains.strip()
            say(
                f"   Config account.kind is {config.account.kind!r}"
                + (f" and account_name_contains is {needle!r}." if needle else ".")
                + " This check does not trade."
            )
            if any(item.simulated is True and item.name.upper().startswith("50KTC") for item in combines):
                say(
                    "   Allowed target: simulated=true and the name starts with 50KTC. "
                    "A Live Funded account (simulated=false, or a name containing LIVE) is still refused."
                )
            say(
                "   The bot's own daily stop in config is $400. "
                "A Standard 50K Combine can also have Topstep's $1,000 Daily Loss Limit. "
                "This check does not change either limit."
            )
            steps["accounts"] = {
                "status": "PASS",
                "summary": summary,
                "request": {"onlyActiveAccounts": True},
                "response": account_body,
                "combine_ids": [item.id for item in combines],
            }

    say("")
    say(f"3. Contracts  POST /api/Contract/search  searchText={config.instrument.symbol!r}")
    contract_id = config.instrument.contract_id.strip()
    picked: dict | None = None
    try:
        contract_body = client.search_contracts_raw(
            config.instrument.symbol,
            live=config.broker.projectx_use_live_data,
        )
    except ProjectXError as exc:
        say(f"   FAIL  {exc}")
        steps["contracts"] = {"status": "FAIL", "summary": "contract search failed", "error": str(exc)}
        contract_body = None
    if contract_body is not None:
        returned = list(contract_body.get("contracts") or [])
        if not returned:
            say("   No contracts were returned.")
        for contract in returned:
            say(f"   {_format_contract(contract)}")
        try:
            picked = pick_front_month(returned, config.instrument.symbol)
        except ProjectXError as exc:
            picked = None
            say(f"   Could not choose a front month: {exc}")
        if contract_id:
            say(
                f"   Front month the bot would use: {contract_id} "
                "(pinned by instrument.contract_id in config/settings.yaml)."
            )
            if picked is not None:
                say(
                    f"   Contract/search would otherwise pick id={picked.get('id')} "
                    f"name={picked.get('name')} (first active MES match in the order returned)."
                )
            steps["contracts"] = {
                "status": "PASS",
                "summary": f"pinned {contract_id}",
                "request": {
                    "searchText": config.instrument.symbol,
                    "live": config.broker.projectx_use_live_data,
                },
                "response": contract_body,
                "picked": picked,
                "used_contract_id": contract_id,
            }
        elif picked is not None:
            say(
                f"   Front month the bot would pick: id={picked.get('id')} name={picked.get('name')} "
                "tickSize="
                f"{picked.get('tickSize')} tickValue={picked.get('tickValue')} "
                "(first active MES match in the order Contract/search returned)."
            )
            contract_id = str(picked.get("id") or "")
            steps["contracts"] = {
                "status": "PASS",
                "summary": f"front month {picked.get('id')} {picked.get('name')}",
                "request": {
                    "searchText": config.instrument.symbol,
                    "live": config.broker.projectx_use_live_data,
                },
                "response": contract_body,
                "picked": picked,
                "used_contract_id": contract_id,
            }
        else:
            steps["contracts"] = {
                "status": "FAIL",
                "summary": "no MES front month",
                "request": {
                    "searchText": config.instrument.symbol,
                    "live": config.broker.projectx_use_live_data,
                },
                "response": contract_body,
            }

    say("")
    say("4. Bars  POST /api/History/retrieveBars  5-minute, about the last day")
    if not contract_id:
        say("   FAIL  No contract id, so bars were not requested.")
        steps["bars"] = {"status": "FAIL", "summary": "no contract id"}
    else:
        end = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
        start = end - timedelta(days=1)
        bar_request = {
            "contractId": contract_id,
            "live": config.broker.projectx_use_live_data,
            "startTime": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endTime": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "unit": 2,
            "unitNumber": 5,
            "limit": 400,
            "includePartialBar": False,
        }
        try:
            bar_body = client.retrieve_bars_raw(
                contract_id,
                start,
                end,
                unit=2,
                unit_number=5,
                limit=400,
                live=config.broker.projectx_use_live_data,
                include_partial=False,
            )
        except ProjectXError as exc:
            say(f"   FAIL  {exc}")
            steps["bars"] = {
                "status": "FAIL",
                "summary": "retrieveBars failed",
                "request": bar_request,
                "error": str(exc),
            }
        else:
            bars = list(bar_body.get("bars") or [])
            order = classify_bar_order(bars)
            say(f"   count: {len(bars)}")
            if bars:
                say(f"   first timestamp (as returned): {bars[0].get('t')}")
                say(f"   last timestamp (as returned): {bars[-1].get('t')}")
            else:
                say("   first timestamp (as returned): none")
                say("   last timestamp (as returned): none")
            say(f"   arrival order: {order}")
            say("   Confirmed shape is newest-first. The trading client sorts these oldest-first. The check does not.")
            steps["bars"] = {
                "status": "PASS",
                "summary": f"count={len(bars)} order={order}",
                "request": bar_request,
                "response": bar_body,
                "count": len(bars),
                "arrival_order": order,
            }

    say("")
    say(f"5. SignalR market hub  MES quotes for {signalr_seconds:g} seconds")
    hub_url = _safe_hub_url(getattr(client, "market_hub_url", ""), secrets)
    if hub_url:
        say(f"   hub: {hub_url} (token not shown)")
    market_open = cme_equity_index_open(moment)
    if skip_signalr:
        say("   SKIP  --no-signalr was set.")
        steps["signalr"] = {"status": "SKIP", "summary": "skipped", "hub": hub_url}
    elif not client.token or not contract_id:
        say("   SKIP  No token or contract id, so the hub was not opened.")
        steps["signalr"] = {"status": "SKIP", "summary": "not attempted", "hub": hub_url}
    else:
        listener = listen_quotes or listen_market_quotes
        try:
            result = listener(client, contract_id, signalr_seconds, sleep=sleep or time.sleep)
        except Exception as exc:  # noqa: BLE001 — a hub failure must not abort the summary
            result = {"ok": False, "error": exc, "quotes": []}
        quotes = list(result.get("quotes") or [])
        printed_hub = result.get("public_url") or hub_url
        if printed_hub and printed_hub != hub_url:
            say(f"   hub: {printed_hub} (token not shown)")
        say(f"   quotes arrived: {len(quotes)}")
        if quotes:
            say(f"   sample: {_format_quote(quotes[0])}")
        status, summary, detail = _signalr_status(len(quotes), bool(result.get("ok")), result.get("error"), market_open, secrets)
        say(f"   {status}  {detail}")
        say("   Disconnected.")
        steps["signalr"] = {
            "status": status,
            "summary": summary,
            "hub": printed_hub or hub_url,
            "market_open": market_open,
            "error": _safe_error(result.get("error"), secrets) if result.get("error") else "",
            "quote_count": len(quotes),
            "sample": quotes[0] if quotes else None,
        }

    return _finish(directory, moment, steps, secrets, say)


def identify_50k_combines(accounts: list[AccountInfo]) -> list[AccountInfo]:
    """Accounts whose names look like a Topstep 50K Trading Combine.

    Topstep combine names usually contain ``50K`` and ``TC`` (for example
    ``50KTC-...``) or the word COMBINE. ``150K`` does not count as 50K.
    """
    found = []
    for account in accounts:
        name = account.name.upper()
        combine = "COMBINE" in name or "TC" in name
        if combine and _50K_NAME.search(account.name):
            found.append(account)
    return found


def classify_bar_order(bars: list[dict]) -> str:
    """Say whether the raw `t` strings arrived newest-first, oldest-first, or unsorted.

    Comparison uses the timestamp strings as returned. It does not sort the bars.
    """
    times = [str(bar.get("t")) for bar in bars]
    if len(times) < 2:
        return "too-few-bars"
    ascending = times == sorted(times)
    descending = times == sorted(times, reverse=True)
    if ascending and descending:
        return "equal-timestamps"
    if descending:
        return "newest-first"
    if ascending:
        return "oldest-first"
    return "unsorted"


def listen_market_quotes(client: ReadOnlyProjectXClient, contract_id: str, seconds: float, sleep=time.sleep) -> dict:
    """Connect, count GatewayQuote events, then stop the hub."""
    quotes: list[dict] = []
    public = ""
    try:
        public = public_hub_url(client.market_hub_url)
    except ProjectXError:
        public = ""

    def on_quote(*args) -> None:
        quotes.append(normalize_quote(args))

    hub = None
    try:
        hub = client.connect_quotes(contract_id, on_quote)
        public = getattr(hub, "public_url", None) or public
        sleep(max(0.0, seconds))
        failure = getattr(hub, "error", None)
        if failure is not None and not quotes:
            return {"ok": False, "error": failure, "quotes": quotes, "public_url": public}
    except Exception as exc:  # noqa: BLE001 — handshake errors are reported, not raised
        return {"ok": False, "error": exc, "quotes": quotes, "public_url": public}
    finally:
        if hub is not None:
            stop = getattr(hub, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
    return {"ok": True, "error": None, "quotes": quotes, "public_url": public}


def _signalr_status(quote_count: int, ok: bool, error: object, market_open: bool, secrets: list[str]) -> tuple[str, str, str]:
    """FAIL when the session is open and no quote arrived. WARN only when it is closed."""
    if not ok:
        safe = _safe_error(error, secrets)
        return "FAIL", "handshake failed", f"Market hub failed: {safe}"
    if quote_count > 0:
        return "PASS", f"{quote_count} quotes", f"{quote_count} quotes"
    if market_open:
        return (
            "FAIL",
            "0 quotes while MES is open",
            "0 quotes while the CME equity-index session is open. The bot will not trade without a live quote.",
        )
    return (
        "WARN",
        "0 quotes; MES session is closed",
        "0 quotes. The CME equity-index session is closed (halt 16:00–17:00 CT, or the weekend). Zero quotes are expected.",
    )


def _safe_hub_url(base: str, secrets: list[str]) -> str:
    if not base:
        return ""
    try:
        return redact_text(public_hub_url(base), secrets)
    except ProjectXError:
        return ""


def normalize_quote(args: tuple) -> dict:
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        args = tuple(args[0])
    contract = None
    data: object
    if len(args) >= 2 and isinstance(args[1], dict):
        contract = args[0]
        data = args[1]
    elif len(args) == 1 and isinstance(args[0], dict):
        data = args[0]
    else:
        data = {"args": [_jsonable(item) for item in args]}
    return {"contractId": contract, "data": data}


def _format_account(account: AccountInfo) -> str:
    raw = account.raw
    return (
        f"id={account.id}  name={account.name}  "
        f"balance={_field(raw, 'balance')}  canTrade={_field(raw, 'canTrade')}  "
        f"simulated={_field(raw, 'simulated')}"
    )


def _format_contract(contract: dict) -> str:
    return (
        f"id={contract.get('id')}  name={contract.get('name')}  "
        f"tickSize={contract.get('tickSize')}  tickValue={contract.get('tickValue')}  "
        f"activeContract={contract.get('activeContract')}  symbolId={contract.get('symbolId')}"
    )


def _format_quote(quote: dict) -> str:
    data = quote.get("data") if isinstance(quote, dict) else None
    if not isinstance(data, dict):
        return str(quote)
    pieces = []
    for key in ("lastPrice", "bestBid", "bestAsk", "symbol"):
        if key in data:
            pieces.append(f"{key}={data[key]}")
    if not pieces:
        pieces.append("keys=" + ",".join(str(key) for key in list(data)[:8]))
    contract = quote.get("contractId")
    if contract:
        pieces.insert(0, f"contractId={contract}")
    return " ".join(pieces)


def _field(raw: dict, key: str) -> str:
    if key not in raw:
        return "field absent"
    value = raw[key]
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _safe_error(error: object, secrets: list[str]) -> str:
    if error is None:
        return "unknown error"
    if isinstance(error, BaseException):
        text = f"{type(error).__name__}: {error}"
    else:
        text = str(error)
    text = redact_text(text, secrets)
    if len(text) > 300:
        text = text[:300] + "..."
    return text


def _finish(directory: Path, moment: datetime, steps: dict, secrets: list[str], say) -> int:
    for name in _STEP_ORDER:
        steps.setdefault(name, {"status": "SKIP", "summary": "not run"})
    say("")
    say("SUMMARY")
    for name in _STEP_ORDER:
        step = steps[name]
        say(f"  {name:<10} {step['status']:<4}  {step.get('summary', '')}")
    required = [steps[name]["status"] for name in ("auth", "accounts", "contracts", "bars")]
    rest_ok = all(status == "PASS" for status in required)
    signal = steps["signalr"]["status"]
    overall = "PASS" if rest_ok and signal != "FAIL" else "FAIL"
    say(f"OVERALL {overall}")
    if signal == "FAIL" and rest_ok:
        say("SignalR FAIL. Login and market data were readable, but there is no live MES quote. Practice mode will stay flat.")
    elif signal == "WARN" and overall == "PASS":
        say("SignalR WARN. The MES session is closed, so zero quotes are not a failed feed. This check did not trade.")
    _write_report(directory, moment, steps, secrets, say)
    return 0 if overall == "PASS" else 1


def _skip_after(steps: dict, failed: str) -> None:
    seen = False
    for name in _STEP_ORDER:
        if name == failed:
            seen = True
            continue
        if seen:
            steps[name] = {"status": "SKIP", "summary": "not run"}


def _write_report(directory: Path, moment: datetime, steps: dict, secrets: list[str], say) -> None:
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"check-{stamp}.json"
    payload = {
        "generated_at": moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "allowed_paths": sorted(READ_ONLY_PATHS),
        "steps": steps,
        "notes": [
            "The allowlist has no order or position route. The check client raises before sending one.",
            "token, apiKey, and Authorization values are replaced with [REDACTED].",
            "Swagger TradingAccountModel (api.topstepx.com, 2026-10-07) requires id, name, balance, canTrade, isVisible, and simulated. Compare that with the accounts response in this file.",
        ],
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(redact(payload, secrets), indent=2, default=str) + "\n", encoding="utf-8")
    except OSError as exc:
        say(f"Could not write {path.name}: {type(exc).__name__}")
        return
    say(f"Wrote {path.as_posix()}")


def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)

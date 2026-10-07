"""Download 1-minute bars through the ProjectX history endpoint."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from topstepbot.bars import write_bars
from topstepbot.broker.projectx import ProjectXClient, pick_front_month
from topstepbot.config import BotConfig
from topstepbot.models import Bar


def download_bars(config: BotConfig, start_text: str, end_text: str, out: Path) -> int:
    load_dotenv()
    username = os.environ.get("PROJECTX_USERNAME", "").strip()
    api_key = os.environ.get("PROJECTX_API_KEY", "").strip()
    if not username or not api_key:
        raise SystemExit("Set PROJECTX_USERNAME and PROJECTX_API_KEY in .env before downloading.")
    tz = ZoneInfo(config.session.timezone)
    start = _parse(start_text, tz)
    end = _parse(end_text, tz)
    client = ProjectXClient(
        username=username,
        api_key=api_key,
        api_url=os.environ.get("PROJECTX_API_URL", "https://api.topstepx.com"),
        user_hub_url=os.environ.get("PROJECTX_USER_HUB_URL", "https://rtc.topstepx.com/hubs/user"),
        market_hub_url=os.environ.get("PROJECTX_MARKET_HUB_URL", "https://rtc.topstepx.com/hubs/market"),
    )
    client.login()
    if config.instrument.contract_id:
        contract_id = config.instrument.contract_id
    else:
        contracts = client.available_contracts(live=config.broker.projectx_use_live_data)
        contract_id = pick_front_month(contracts, config.instrument.symbol)["id"]
    bars: list[Bar] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=1), end)
        bars.extend(
            client.retrieve_bars(
                contract_id,
                cursor,
                chunk_end,
                live=config.broker.projectx_use_live_data,
            )
        )
        cursor = chunk_end
    bars.sort(key=lambda bar: bar.time)
    unique: list[Bar] = []
    seen = set()
    for bar in bars:
        if bar.time in seen:
            continue
        seen.add(bar.time)
        unique.append(bar)
    write_bars(
        out,
        unique,
        comment=f"MES 1-minute bars from ProjectX contract {contract_id}. Not synthetic.",
    )
    return len(unique)


def _parse(text: str, tz: ZoneInfo) -> datetime:
    raw = text.strip()
    if len(raw) == 10:
        raw = raw + "T00:00:00"
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment.astimezone(timezone.utc)

"""Download MES 1-minute history without any way to place an order.

Uses the read-only ProjectX client. History requests stay inside the
documented retrieveBars bucket (50 calls per 30 seconds) and back off on
HTTP 429. Each request is one Central-time day, under the 20,000 bar cap.

This process cannot discover how far back ProjectX will actually go until
it is run with the key on the trader's Mac. Expired quarter codes are
searched (MESU6, MESM6, and the four-digit form). If search does not return
a contract, that is printed and that quarter is skipped.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from topstepbot.broker.projectx import ProjectXError
from topstepbot.broker.ratelimit import HISTORY_PATH, RateHalt, SlidingWindowLimiter
from topstepbot.broker.readonly import ReadOnlyProjectXClient, ReadOnlyViolation
from topstepbot.timeutil import CHICAGO

DEFAULT_DAYS = 365
BAR_LIMIT = 20000
_QUARTERS = (("H", 3), ("M", 6), ("U", 9), ("Z", 12))
_STOP = object()


def fetch_history(
    *,
    days: int = DEFAULT_DAYS,
    out: str | Path = "data/mes_1m_real.csv",
    client: ReadOnlyProjectXClient | None = None,
    say=print,
    limiter: SlidingWindowLimiter | None = None,
    sleep=None,
    now: datetime | None = None,
) -> int:
    """Write a resume-safe CSV. Returns how many bars are in the file."""
    if days < 1:
        raise SystemExit("Days must be at least 1.")
    if client is None:
        client = _client_from_env()
        _require_readonly(client)
        say("Logging in. The API key is not printed.")
        client.login()
    else:
        _require_readonly(client)
    destination = Path(out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    clock_sleep = sleep or __import__("time").sleep
    pacing = limiter or SlidingWindowLimiter(clock=__import__("time").monotonic, sleep=clock_sleep)
    moment = now or datetime.now(CHICAGO)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CHICAGO)
    end = moment.astimezone(CHICAGO).replace(second=0, microsecond=0)
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    say(
        f"Asking ProjectX for 1-minute MES bars from {start.date()} through {end.date()} "
        f"Central time ({days} days). One day per request. "
        "History calls wait so they stay under 50 every 30 seconds."
    )
    existing = _read_csv(destination)
    contracts, missing = _find_contracts(client, pacing, start, end, say, clock_sleep)
    if not contracts:
        raise SystemExit(
            "ProjectX contract search did not return any MES contract. "
            "Nothing was written. No order was sent."
        )
    rows = dict(existing)
    stopped = False
    for contract_id, name in contracts:
        have_days = {stamp.astimezone(CHICAGO).date() for (saved_name, stamp) in rows if saved_name == name}
        day = start.date()
        last_day = end.date()
        say(f"Contract {name}.")
        while day <= last_day:
            if day < last_day and day in have_days:
                day += timedelta(days=1)
                continue
            day_start = datetime(day.year, day.month, day.day, tzinfo=CHICAGO)
            day_end = min(day_start + timedelta(days=1), end + timedelta(minutes=1))
            if day_start >= day_end:
                break
            fetched = _fetch_span(client, pacing, contract_id, name, day_start, day_end, say, clock_sleep)
            if fetched is _STOP:
                stopped = True
                break
            for row in fetched:
                rows[(name, row[0])] = row
            say(f"{name} {day.isoformat()}: {len(fetched)} bars. File holds {len(rows)} bars.")
            _write_csv(destination, rows)
            day += timedelta(days=1)
        if stopped:
            break
    _write_csv(destination, rows)
    if stopped:
        say("Stopped early because ProjectX kept rate-limiting. The file has every day that finished.")
    if rows:
        stamps = [row[0] for row in rows.values()]
        say(
            f"Done. {len(rows)} bars in {destination}. "
            f"Oldest {min(stamps).isoformat()}, newest {max(stamps).isoformat()}."
        )
    else:
        say(f"Done. ProjectX returned no bars. {destination} has a header only.")
    if missing:
        say(
            "ProjectX search did not return these quarter codes, so those expired months "
            "are not in the file: " + ", ".join(missing) + ". "
            "The file has only the contracts search did return."
        )
    say("This command cannot place, change, or cancel an order.")
    return len(rows)


def quarter_search_texts(start: datetime, end: datetime) -> list[str]:
    """MES plus each quarter code from just before the window through the front month."""
    texts = ["MES"]
    seen = {"MES"}
    cursor = start.astimezone(CHICAGO).date().replace(day=1) - timedelta(days=100)
    stop = end.astimezone(CHICAGO).date() + timedelta(days=100)
    while cursor <= stop:
        for code, month in _QUARTERS:
            year = cursor.year
            if month < cursor.month and cursor.month - month > 6:
                year += 1
            # Keep a code when that expiry is near the window.
            expiry_year = year if month >= 1 else year
            if not _quarter_near(expiry_year, month, start, end):
                continue
            for label in (f"MES{code}{expiry_year % 10}", f"MES{code}{expiry_year % 100:02d}"):
                if label not in seen:
                    seen.add(label)
                    texts.append(label)
        cursor += timedelta(days=32)
        cursor = cursor.replace(day=1)
    return texts


def _quarter_near(year: int, month: int, start: datetime, end: datetime) -> bool:
    expiry = datetime(year, month, 15, tzinfo=CHICAGO)
    window_start = start.astimezone(CHICAGO) - timedelta(days=120)
    window_end = end.astimezone(CHICAGO) + timedelta(days=120)
    return window_start <= expiry <= window_end


def _find_contracts(client, limiter, start, end, say, sleep) -> tuple[list[tuple[str, str]], list[str]]:
    found: dict[str, str] = {}
    missing: list[str] = []
    for text in quarter_search_texts(start, end):
        body = _call(client, limiter, "search", text, say, sleep, lambda: client.search_contracts_raw(text, live=False))
        if body is None:
            missing.append(text)
            continue
        added = 0
        for raw in body.get("contracts") or []:
            if not isinstance(raw, dict):
                continue
            contract_id = str(raw.get("id") or "")
            name = str(raw.get("name") or contract_id)
            blob = f"{name} {contract_id}".upper()
            if "MES" not in blob:
                continue
            if contract_id and contract_id not in found:
                found[contract_id] = name
                added += 1
        if text != "MES" and added == 0:
            missing.append(text)
    say(
        "Contracts search returned: "
        + (", ".join(f"{name} ({contract_id})" for contract_id, name in found.items()) or "none")
        + "."
    )
    return list(found.items()), missing


def _fetch_span(client, limiter, contract_id, name, start, end, say, sleep) -> list[tuple]:
    body = _call(
        client,
        limiter,
        "bars",
        name,
        say,
        sleep,
        lambda: client.retrieve_bars_raw(
            contract_id,
            start,
            end,
            unit=2,
            unit_number=1,
            limit=BAR_LIMIT,
            live=False,
            include_partial=False,
        ),
    )
    if body is None or body is _STOP:
        return body if body is _STOP else []
    rows = _rows_from_body(body, name)
    if len(rows) >= BAR_LIMIT:
        say(
            f"{name} returned {BAR_LIMIT} bars for one slice, which is the ProjectX cap, "
            "so that slice is split in half and requested again."
        )
        mid = start + (end - start) / 2
        left = _fetch_span(client, limiter, contract_id, name, start, mid, say, sleep)
        if left is _STOP:
            return _STOP
        right = _fetch_span(client, limiter, contract_id, name, mid, end, say, sleep)
        if right is _STOP:
            return left
        return left + right
    return rows


def _rows_from_body(body: dict, name: str) -> list[tuple]:
    rows = []
    for raw in body.get("bars") or []:
        if not isinstance(raw, dict) or "t" not in raw:
            continue
        moment = datetime.fromisoformat(str(raw["t"]).replace("Z", "+00:00")).astimezone(CHICAGO)
        rows.append(
            (
                moment.replace(second=0, microsecond=0),
                float(raw["o"]),
                float(raw["h"]),
                float(raw["l"]),
                float(raw["c"]),
                float(raw.get("v") or 0),
                name,
            )
        )
    return rows


def _call(client, limiter, kind: str, label: str, say, sleep, fn):
    path = HISTORY_PATH if kind == "bars" else "/api/Contract/search"
    while True:
        try:
            limiter.before_request(path)
        except RateHalt as exc:
            say(f"Stopped. {exc} Bars saved so far are already in the file.")
            return _STOP
        try:
            body = fn()
        except ProjectXError as exc:
            text = str(exc)
            if "429" in text:
                retry = getattr(client, "last_retry_after", None)
                try:
                    delay = limiter.note_failure(retry)
                except RateHalt as halt:
                    say(f"Stopped after repeated rate limits. {halt}")
                    return _STOP
                say(f"ProjectX asked for a pause ({label}). Waiting {delay:.0f} seconds.")
                sleep(delay)
                continue
            say(f"{label} failed: {text}")
            return None
        limiter.note_success()
        return body


def _read_csv(path: Path) -> dict[tuple[str, datetime], tuple]:
    if not path.exists():
        return {}
    rows: dict[tuple[str, datetime], tuple] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            if not raw or not raw.get("timestamp"):
                continue
            moment = datetime.fromisoformat(raw["timestamp"])
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=CHICAGO)
            moment = moment.astimezone(CHICAGO).replace(second=0, microsecond=0)
            name = raw.get("contract") or ""
            rows[(name, moment)] = (
                moment,
                float(raw["open"]),
                float(raw["high"]),
                float(raw["low"]),
                float(raw["close"]),
                float(raw["volume"]),
                name,
            )
    return rows


def _write_csv(path: Path, rows: dict) -> None:
    ordered = sorted(rows.values(), key=lambda row: (row[0], row[6]))
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "contract"])
        for moment, open_, high, low, close, volume, name in ordered:
            writer.writerow(
                [
                    moment.astimezone(CHICAGO).isoformat(),
                    f"{open_:.4f}",
                    f"{high:.4f}",
                    f"{low:.4f}",
                    f"{close:.4f}",
                    f"{volume:.0f}",
                    name,
                ]
            )
    os.replace(temporary, path)


def _require_readonly(client: object) -> None:
    place = getattr(client, "place_order", None)
    if not callable(place):
        raise SystemExit("History download refused a client that is not the read-only connection.")
    try:
        place({})
    except ReadOnlyViolation:
        return
    raise SystemExit("History download refused a client that can place orders. No request was sent.")


def _client_from_env() -> ReadOnlyProjectXClient:
    from dotenv import load_dotenv

    load_dotenv()
    username = os.environ.get("PROJECTX_USERNAME", "").strip()
    api_key = os.environ.get("PROJECTX_API_KEY", "").strip()
    if not username or not api_key:
        raise SystemExit("Set PROJECTX_USERNAME and PROJECTX_API_KEY in .env. The values are not printed.")
    return ReadOnlyProjectXClient(
        username=username,
        api_key=api_key,
        api_url=os.environ.get("PROJECTX_API_URL", "https://api.topstepx.com"),
        market_hub_url=os.environ.get("PROJECTX_MARKET_HUB_URL", "https://rtc.topstepx.com/hubs/market"),
    )

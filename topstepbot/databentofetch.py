"""Download history from Databento.

The default symbol is ``MES.v.0``. ``--symbol MNQ.v.0`` is the same request
for Micro Nasdaq. The ``v`` rule is the continuous front month, ranked by the
previous day's volume (Databento roll rule ``v``, rank 0). ``MES.c.0`` is the
calendar rule and can stay on a quiet expiry after volume has moved, so it is
not used. ``MES.n.0`` would rank by open interest.

Dataset ``GLBX.MDP3``, ``stype_in=continuous``. The default schema is
``ohlcv-1m``. ``--schema`` also accepts ``trades``, ``tbbo``, and ``mbp-1``.
A continuous symbol can only be resolved to ``instrument_id``. The outright
symbol (``MESU4``, or ``MNQH4`` for an MNQ download) comes from a free
``symbology.resolve`` of those ids. Instrument ids are only unique within a
day, so the contract column is ``MESU4:instrument_id`` or ``MNQH4:instrument_id``.

Prices are the original outright prints. Databento does not back-adjust.
A roll is a real price gap. ``history-report`` starts indicators and the
opening range over when the contract column changes.

``metadata.get_cost`` runs before any ``timeseries.get_range``. The download
stops when that price is above ``--max-cost`` unless ``--yes`` is passed.
``--quote-only`` prints that cost and exits without downloading. The API key
is read from ``DATABENTO_API_KEY`` and is never printed.

Tick schemas are written as one compressed file per Chicago calendar day.
``ts_event`` is Central time. ``side`` is Databento's letter: ``A`` is a sell
aggressor, ``B`` is a buy aggressor, ``N`` has no side. ``tbbo`` and ``mbp-1``
also store the bid and ask price and size. On ``tbbo`` that book is the BBO
immediately before the trade. ``mbp-1`` also stores ``action``; ``T`` is a trade
and the other letters are top-of-book updates. Parquet is used when pyarrow is
installed. Otherwise the day file is a gzipped CSV.

``--sessions-only`` keeps each weekday's 08:00–11:00 CT window.
``--sessions-only rth`` keeps 08:30–15:00 CT, the cash session used in the
research note. Omitting the flag requests the whole clock. Databento prices
one continuous start/end, including a partial day. It has no single call for
"every morning," so those windows are quoted separately and added.

The official ``databento`` package is used. A pip dry-run for CPython 3.14
on macOS (arm64 and x86_64) resolves ``databento`` 0.87.0 with
``databento-dbn`` 0.70.0, pandas, numpy, and pyarrow (checked 2026-10-09).
Python 3.14 support started in databento 0.68.0.
"""

from __future__ import annotations

import csv
import gzip
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from topstepbot.historyfetch import _read_csv, _write_csv
from topstepbot.redact import redact_text
from topstepbot.timeutil import CHICAGO

DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
SCHEMAS = ("ohlcv-1m", "trades", "tbbo", "mbp-1")
TICK_SCHEMAS = ("trades", "tbbo", "mbp-1")
# morning is 08:00–11:00 CT. rth is the research cash session, 08:30–15:00 CT.
SESSION_WINDOWS = {"morning": (8 * 60, 11 * 60), "rth": (8 * 60 + 30, 15 * 60)}
SYMBOL = "MES.v.0"
STYPE_IN = "continuous"
# Databento fixed-point prices use 1e-9. A real MES or MNQ print is far below this.
_FIXED_POINT = 1_000_000_000
_PRICE_CEILING = 100_000.0
DEFAULT_MAX_COST = 20.0
DEFAULT_BAR_OUT = "data/mes_1m_databento.csv"
TICK_COLUMNS = {
    "trades": ("ts_event", "price", "size", "side", "contract"),
    "tbbo": ("ts_event", "price", "size", "side", "bid_px", "ask_px", "bid_sz", "ask_sz", "contract"),
    "mbp-1": (
        "ts_event",
        "price",
        "size",
        "side",
        "action",
        "bid_px",
        "ask_px",
        "bid_sz",
        "ask_sz",
        "contract",
    ),
}


def fetch_databento(
    *,
    start: str,
    out: str | Path = DEFAULT_BAR_OUT,
    end: str | None = None,
    max_cost: float = DEFAULT_MAX_COST,
    yes: bool = False,
    symbol: str = SYMBOL,
    schema: str = SCHEMA,
    quote_only: bool = False,
    sessions_only: str | None = None,
    client=None,
    say=print,
    now: datetime | None = None,
    sleep=time.sleep,
) -> int:
    """Write a resume-safe file. Returns how many bars or tick rows were saved.

    ``start`` and ``end`` are UTC. A plain date is midnight UTC. ``end`` is
    exclusive. Bar CSV timestamps, and tick ``ts_event`` values, are Central time.
    ``quote_only`` prints ``metadata.get_cost`` and does not download.
    """
    if max_cost < 0:
        raise SystemExit("max-cost cannot be negative. Nothing was downloaded.")
    symbol = str(symbol).strip()
    if not symbol:
        raise SystemExit("The symbol is empty. Nothing was downloaded.")
    schema = str(schema).strip()
    if schema not in SCHEMAS:
        raise SystemExit(
            f"Schema {schema!r} is not one of {', '.join(SCHEMAS)}. Nothing was downloaded."
        )
    if sessions_only is not None and sessions_only not in SESSION_WINDOWS:
        raise SystemExit(
            "sessions-only must be morning (08:00-11:00 CT) or rth (08:30-15:00 CT). "
            "Nothing was downloaded."
        )
    if client is None:
        client = _client_from_env()
    secrets = _secrets(client)

    def speak(message: str) -> None:
        say(_clean(str(message), secrets))

    destination = Path(out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    window_start = _parse_user_time(start)
    window_end = _parse_user_time(end) if end else _default_end(now or datetime.now(timezone.utc))
    if window_start >= window_end:
        raise SystemExit("The end of the request is not after the start. Nothing was downloaded.")

    speak(
        f"Requesting {symbol} as a continuous symbol on {DATASET}, schema {schema}. "
        "The v roll rule is the front month by the previous day's volume. "
        "The c rule follows the calendar expiry and can linger on a quiet month, so it is not used. "
        "The n rule ranks by open interest. "
        "Prices are the original outright prints. Databento does not back-adjust them. "
        "The contract column stores the outright symbol and the instrument id, so a roll is a new name and a price gap. "
        "An instrument id is only unique within one day, which is why both are written."
    )
    if schema in TICK_SCHEMAS:
        speak(
            "Databento side A is a sell aggressor and side B is a buy aggressor. N has no side. "
            "tbbo bid and ask are the book immediately before the trade."
        )
    if sessions_only == "morning":
        speak("The request keeps each weekday from 08:00 to 11:00 CT and drops the rest of the clock.")
    elif sessions_only == "rth":
        speak("The request keeps each weekday cash session from 08:30 to 15:00 CT and drops the rest of the clock.")
    try:
        bounds = client.dataset_bounds(schema)
    except Exception as exc:
        speak(
            "Could not read how far back this dataset goes. The dates you asked for will be priced. "
            + _clean(str(exc), secrets)
        )
        bounds = (None, None)
    else:
        avail_start, avail_end = _pair(bounds)
        if avail_start is not None and window_start < avail_start:
            speak(
                f"{DATASET} {schema} is available from {avail_start.isoformat()} UTC, "
                f"which is after {window_start.isoformat()} UTC. The download starts at the available time."
            )
            window_start = avail_start
        if avail_end is not None and window_end > avail_end:
            speak(
                f"{DATASET} {schema} is available through {avail_end.isoformat()} UTC. "
                "The download stops there."
            )
            window_end = avail_end
    if window_start >= window_end:
        raise SystemExit("The requested window is outside the dataset range. Nothing was downloaded.")

    if schema != SCHEMA or sessions_only is not None:
        return _fetch_by_window(
            client=client,
            secrets=secrets,
            speak=speak,
            symbol=symbol,
            schema=schema,
            sessions_only=sessions_only,
            window_start=window_start,
            window_end=window_end,
            out=destination,
            max_cost=max_cost,
            yes=yes,
            quote_only=quote_only,
            sleep=sleep,
        )

    existing = _read_csv(destination)
    if existing:
        newest = max(row[0] for row in existing.values())
        resume_at = newest.astimezone(timezone.utc) + timedelta(minutes=1)
        if resume_at > window_start:
            speak(
                f"The file already has bars through {newest.astimezone(CHICAGO).isoformat()} Central time. "
                f"The cost below is only for the remaining span, from {resume_at.isoformat()} UTC "
                f"up to but not including {window_end.isoformat()} UTC."
            )
            window_start = resume_at
    if window_start >= window_end:
        speak(f"The file already covers this request. {len(existing)} bars in {destination}. Nothing was downloaded.")
        return len(existing)

    cost = _quote_spans(
        client,
        [(window_start, window_end)],
        symbol,
        schema,
        speak,
        secrets,
        sleep,
    )
    price = usd_text(cost)
    cap = usd_text(max_cost)
    speak(
        f"The exact cost is {price} US dollars. "
        f"That is the price of {symbol} {schema} on {DATASET} from {window_start.isoformat()} UTC "
        f"up to but not including {window_end.isoformat()} UTC, before any bars are downloaded. "
        f"The cap is {cap} US dollars."
    )
    if quote_only:
        _speak_quote_only(speak, cost, max_cost, price, cap)
        return 0
    if cost > max_cost and not yes:
        message = (
            f"The exact cost is {price} US dollars, which is above the {cap} US dollar cap. "
            "Nothing was downloaded. Pass --yes to download anyway, or raise --max-cost."
        )
        speak(message)
        raise SystemExit(message)
    if cost > max_cost:
        speak(
            f"The exact cost is {price} US dollars, which is above the {cap} US dollar cap. "
            "Continuing because --yes was passed."
        )

    rows = dict(existing)
    for chunk_start, chunk_end in month_slices(window_start, window_end):
        fetched = _guard(
            speak,
            secrets,
            lambda start=chunk_start, finish=chunk_end: client.bars(start, finish, symbol, schema),
        )
        added = 0
        for record in fetched:
            written = row_from_record(record)
            if written is None:
                continue
            moment, open_, high, low, close, volume, name = written
            rows[(name, moment)] = (moment, open_, high, low, close, volume, name)
            added += 1
        _write_csv(destination, rows)
        speak(
            f"{chunk_start.date().isoformat()} through {chunk_end.date().isoformat()}: "
            f"{added} bars. File holds {len(rows)} bars."
        )
    if rows:
        stamps = [row[0] for row in rows.values()]
        speak(
            f"Done. {len(rows)} bars in {destination}. "
            f"Oldest {min(stamps).isoformat()}, newest {max(stamps).isoformat()}."
        )
    else:
        speak(f"Done. Databento returned no bars. {destination} has a header only.")
    speak("This command cannot place, change, or cancel an order.")
    return len(rows)


def usd_text(cost: float) -> str:
    """Plain dollar text that keeps every digit the float actually has, to the cent."""
    text = format(float(cost), ".10f").rstrip("0").rstrip(".")
    if "." not in text:
        return text + ".00"
    decimals = text.split(".", 1)[1]
    if len(decimals) < 2:
        return text + ("0" * (2 - len(decimals)))
    return text


def scale_price(value: object) -> float | None:
    """Decimal price. Fixed-point 1e-9 values, and the unset sentinel, become None or a float."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"nan", "none", "null"}:
            return None
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if abs(number) >= _PRICE_CEILING:
        number = number / _FIXED_POINT
    if abs(number) >= _PRICE_CEILING:
        return None
    return number


def contract_label(raw: str, instrument_id: object) -> str:
    ident = _instrument_text(instrument_id)
    raw_name = (raw or "").strip()
    if raw_name and ident:
        return f"{raw_name}:{ident}"
    if raw_name:
        return raw_name
    if ident:
        return f"id:{ident}"
    return ""


def raw_symbol_on(resolved: object, instrument_id: object, moment: datetime) -> str:
    """Outright symbol for this instrument id on this UTC date. Empty when the map has none."""
    if not isinstance(resolved, dict):
        return ""
    result = resolved.get("result") if isinstance(resolved.get("result"), dict) else resolved
    if not isinstance(result, dict):
        return ""
    ident = _instrument_text(instrument_id)
    intervals = result.get(ident) or []
    day = moment.astimezone(timezone.utc).date()
    for interval in intervals:
        if not isinstance(interval, dict):
            continue
        start = _as_date(interval.get("d0") if "d0" in interval else interval.get("start_date"))
        finish = _as_date(interval.get("d1") if "d1" in interval else interval.get("end_date"))
        symbol = str(interval.get("s") or interval.get("symbol") or "").strip()
        if start is None or finish is None or not symbol or symbol.isdigit():
            continue
        if start <= day < finish:
            return symbol
    return ""


def row_from_record(record: tuple) -> tuple | None:
    moment, open_, high, low, close, volume, instrument, raw = record
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(CHICAGO).replace(second=0, microsecond=0)
    prices = [scale_price(open_), scale_price(high), scale_price(low), scale_price(close)]
    if any(price is None for price in prices):
        return None
    open_f, high_f, low_f, close_f = prices
    if high_f < low_f:
        return None
    try:
        vol = float(volume)
    except (TypeError, ValueError):
        return None
    if vol != vol:
        return None
    name = contract_label("" if raw is None else str(raw), instrument)
    return (moment, open_f, high_f, low_f, close_f, vol, name)


def month_slices(start: datetime, end: datetime):
    """UTC month chunks. The last slice stops at ``end``."""
    cursor = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    while cursor < end:
        if cursor.month == 12:
            boundary = datetime(cursor.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            boundary = datetime(cursor.year, cursor.month + 1, 1, tzinfo=timezone.utc)
        nxt = boundary if boundary < end else end
        yield cursor, nxt
        cursor = nxt


def chicago_day_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime, date]]:
    """Every Chicago calendar day that overlaps ``[start, end)``, clipped to that span."""
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    day = start.astimezone(CHICAGO).date()
    last = (end - timedelta(microseconds=1)).astimezone(CHICAGO).date()
    windows = []
    while day <= last:
        open_dt = datetime(day.year, day.month, day.day, tzinfo=CHICAGO)
        close_dt = open_dt + timedelta(days=1)
        lo = max(open_dt.astimezone(timezone.utc), start)
        hi = min(close_dt.astimezone(timezone.utc), end)
        if lo < hi:
            windows.append((lo, hi, day))
        day += timedelta(days=1)
    return windows


def session_windows(start: datetime, end: datetime, kind: str) -> list[tuple[datetime, datetime, date]]:
    """Weekday session windows inside ``[start, end)``. Saturday and Sunday are skipped.

    ``morning`` is 08:00–11:00 CT. ``rth`` is 08:30–15:00 CT. The end minute is exclusive.
    """
    if kind not in SESSION_WINDOWS:
        raise SystemExit(
            "sessions-only must be morning (08:00-11:00 CT) or rth (08:30-15:00 CT). "
            "Nothing was downloaded."
        )
    open_min, close_min = SESSION_WINDOWS[kind]
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    day = start.astimezone(CHICAGO).date()
    last = (end - timedelta(microseconds=1)).astimezone(CHICAGO).date()
    windows = []
    while day <= last:
        if day.weekday() < 5:
            open_dt = datetime(day.year, day.month, day.day, open_min // 60, open_min % 60, tzinfo=CHICAGO)
            close_dt = datetime(day.year, day.month, day.day, close_min // 60, close_min % 60, tzinfo=CHICAGO)
            lo = max(open_dt.astimezone(timezone.utc), start)
            hi = min(close_dt.astimezone(timezone.utc), end)
            if lo < hi:
                windows.append((lo, hi, day))
        day += timedelta(days=1)
    return windows


def collapse_windows(windows: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Merge spans that touch. A gap, including the overnight between two mornings, stays split."""
    if not windows:
        return []
    merged = [[windows[0][0], windows[0][1]]]
    for start, end in windows[1:]:
        if start == merged[-1][1]:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def normalize_tick_row(raw: dict, schema: str) -> dict | None:
    """One order-flow row. Returns None when the price, size, or side cannot be read."""
    if schema not in TICK_COLUMNS:
        return None
    if not isinstance(raw, dict):
        return None
    moment = _as_utc(raw.get("ts_event"))
    if moment is None:
        return None
    price = scale_price(raw.get("price"))
    if price is None:
        return None
    size = _whole_size(raw.get("size"))
    if size is None:
        return None
    side = _side_code(raw.get("side"))
    if side is None:
        return None
    row = {
        "ts_event": moment.astimezone(CHICAGO),
        "price": price,
        "size": size,
        "side": side,
        "contract": contract_label(
            "" if raw.get("raw_symbol") is None else str(raw.get("raw_symbol")),
            raw.get("instrument_id"),
        ),
    }
    if schema in {"tbbo", "mbp-1"}:
        row["bid_px"] = scale_price(raw.get("bid_px"))
        row["ask_px"] = scale_price(raw.get("ask_px"))
        row["bid_sz"] = _whole_size(raw.get("bid_sz"))
        row["ask_sz"] = _whole_size(raw.get("ask_sz"))
    if schema == "mbp-1":
        row["action"] = _action_code(raw.get("action"))
    return row


def tick_output_dir(out: Path, symbol: str, schema: str, sessions_only: str | None = None) -> Path:
    """Directory of per-day tick files. The 1-minute CSV default is not reused.

    The default directory includes the session, so a morning pull does not
    mark a later full-day pull as already downloaded.
    """
    if out.name == Path(DEFAULT_BAR_OUT).name:
        name = f"{_symbol_root(symbol)}_{schema.replace('-', '')}"
        if sessions_only:
            name = f"{name}_{sessions_only}"
        return out.parent / name
    if out.name.endswith(".csv.gz"):
        return out.with_name(out.name[: -len(".csv.gz")])
    if out.suffix.lower() in {".csv", ".parquet"}:
        return out.with_name(out.stem)
    return out


def write_tick_day(directory: Path, day: date, rows: list[dict], schema: str) -> Path:
    """Atomically write one Chicago day. Parquet when pyarrow is installed, else gzipped CSV."""
    directory.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda row: (row["ts_event"], row["contract"], row["price"], row["size"]))
    if _parquet_available():
        final = directory / f"{day.isoformat()}.parquet"
        temporary = directory / f"{day.isoformat()}.parquet.tmp"
        _write_parquet(temporary, ordered, schema)
    else:
        final = directory / f"{day.isoformat()}.csv.gz"
        temporary = directory / f"{day.isoformat()}.csv.gz.tmp"
        _write_csv_gz(temporary, ordered, schema)
    os.replace(temporary, final)
    return final


def day_file(directory: Path, day: date) -> Path | None:
    for name in (f"{day.isoformat()}.parquet", f"{day.isoformat()}.csv.gz"):
        path = directory / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _fetch_by_window(
    *,
    client,
    secrets,
    speak,
    symbol: str,
    schema: str,
    sessions_only: str | None,
    window_start: datetime,
    window_end: datetime,
    out: Path,
    max_cost: float,
    yes: bool,
    quote_only: bool,
    sleep,
) -> int:
    if schema == SCHEMA:
        windows = session_windows(window_start, window_end, sessions_only or "morning")
        existing = _read_csv(out)
        newest = max((row[0] for row in existing.values()), default=None)
        pending = []
        for start, end, day in windows:
            if newest is not None and newest.astimezone(timezone.utc) + timedelta(minutes=1) >= end:
                continue
            pending.append((start, end, day))
        if windows and not pending:
            speak(f"The file already covers this request. {len(existing)} bars in {out}. Nothing was downloaded.")
            return len(existing)
        spans = [(start, end) for start, end, _day in pending]
        label = "weekday window"
    else:
        directory = tick_output_dir(out, symbol, schema, sessions_only)
        if sessions_only:
            windows = session_windows(window_start, window_end, sessions_only)
            pending = [(start, end, day) for start, end, day in windows if day_file(directory, day) is None]
            spans = [(start, end) for start, end, _day in pending]
            label = "weekday window"
        else:
            windows = chicago_day_windows(window_start, window_end)
            pending = [(start, end, day) for start, end, day in windows if day_file(directory, day) is None]
            spans = collapse_windows([(start, end) for start, end, _day in pending])
            label = "Chicago day"
        kept = len(windows) - len(pending)
        if kept:
            speak(
                f"{kept} day files are already in {directory}. "
                "The cost below does not include them."
            )
        if windows and not pending:
            speak(f"The day files already cover this request. Nothing was downloaded. Directory: {directory}.")
            return _count_tick_rows(directory, [day for _start, _end, day in windows])

    if not spans:
        raise SystemExit("No weekday session falls inside this request. Nothing was downloaded.")

    cost = _quote_spans(client, spans, symbol, schema, speak, secrets, sleep)
    price = usd_text(cost)
    cap = usd_text(max_cost)
    if len(spans) == 1:
        speak(
            f"The exact cost is {price} US dollars. "
            f"That is the price of {symbol} {schema} on {DATASET} from {spans[0][0].isoformat()} UTC "
            f"up to but not including {spans[0][1].isoformat()} UTC, before any rows are downloaded. "
            f"The cap is {cap} US dollars."
        )
    else:
        speak(
            f"The exact cost is {price} US dollars. "
            f"That is the sum of {len(spans)} metadata.get_cost calls for {symbol} {schema} on {DATASET}, "
            f"one per {label}. "
            f"The first window starts {spans[0][0].isoformat()} UTC and the last ends {spans[-1][1].isoformat()} UTC, "
            "end exclusive. "
            "Databento prices one continuous start and end, including a partial day. "
            "It has no single call for a union of mornings, so each window is quoted on its own and the prices are added. "
            "A single call from the first start to the last end would include the hours outside these windows, "
            "and that is not this price. "
            f"The cap is {cap} US dollars."
        )
    if quote_only:
        _speak_quote_only(speak, cost, max_cost, price, cap)
        return 0
    if cost > max_cost and not yes:
        message = (
            f"The exact cost is {price} US dollars, which is above the {cap} US dollar cap. "
            "Nothing was downloaded. Pass --yes to download anyway, or raise --max-cost."
        )
        speak(message)
        raise SystemExit(message)
    if cost > max_cost:
        speak(
            f"The exact cost is {price} US dollars, which is above the {cap} US dollar cap. "
            "Continuing because --yes was passed."
        )

    if schema == SCHEMA:
        return _download_bar_windows(client, secrets, speak, symbol, schema, pending, out, existing)
    if sessions_only:
        return _download_session_ticks(client, secrets, speak, symbol, schema, pending, directory)
    return _download_span_ticks(client, secrets, speak, symbol, schema, pending, spans, directory)


def _quote_spans(client, spans, symbol: str, schema: str, speak, secrets, sleep) -> float:
    total = 0.0
    count = len(spans)
    for index, (start, end) in enumerate(spans, start=1):
        part = _as_cost(_cost_with_retry(client, start, end, symbol, schema, speak, secrets, sleep), speak)
        total += part
        if count > 1 and index % 25 == 0 and index != count:
            speak(
                f"Quoted {index} of {count} windows. Running total {usd_text(total)} US dollars. "
                "metadata.get_cost does not download and does not spend credit."
            )
    return total


def _cost_with_retry(client, start, end, symbol, schema, speak, secrets, sleep):
    delay = 2.0
    for attempt in range(6):
        try:
            return client.cost(start, end, symbol, schema)
        except SystemExit:
            raise
        except Exception as exc:
            message = _clean(str(exc), secrets)
            if "429" not in message or attempt == 5:
                speak(message)
                raise SystemExit(message) from None
            speak(message)
            speak(
                f"Databento rate-limited the cost quote. Waiting {delay:.0f} seconds, then retrying. "
                "The key is not printed."
            )
            sleep(delay)
            delay *= 2
    raise SystemExit("Databento did not return a cost. Nothing was downloaded.")


def _as_cost(cost, speak) -> float:
    try:
        number = float(cost)
    except (TypeError, ValueError):
        speak("Databento did not return a numeric cost. Nothing was downloaded.")
        raise SystemExit("Databento did not return a numeric cost. Nothing was downloaded.") from None
    if number != number or number < 0 or number == float("inf"):
        speak("Databento did not return a usable cost. Nothing was downloaded.")
        raise SystemExit("Databento did not return a usable cost. Nothing was downloaded.") from None
    return number


def _speak_quote_only(speak, cost: float, max_cost: float, price: str, cap: str) -> None:
    speak("Quote only. metadata.get_cost does not download and does not spend credit. Nothing was downloaded.")
    if cost > max_cost:
        speak(
            f"A download of this request would stop, because {price} US dollars is above the {cap} US dollar cap. "
            "Pass --yes or raise --max-cost to download it."
        )
    speak("This command cannot place, change, or cancel an order.")


def _download_bar_windows(client, secrets, speak, symbol, schema, pending, out: Path, existing: dict) -> int:
    rows = dict(existing)
    for start, end, day in pending:
        fetched = _guard(
            speak,
            secrets,
            lambda start=start, finish=end: client.bars(start, finish, symbol, schema),
        )
        added = 0
        for record in fetched:
            written = row_from_record(record)
            if written is None:
                continue
            moment, open_, high, low, close, volume, name = written
            rows[(name, moment)] = (moment, open_, high, low, close, volume, name)
            added += 1
        _write_csv(out, rows)
        speak(f"{day.isoformat()}: {added} bars. File holds {len(rows)} bars.")
    speak("This command cannot place, change, or cancel an order.")
    return len(rows)


def _download_session_ticks(client, secrets, speak, symbol, schema, pending, directory: Path) -> int:
    saved = 0
    for start, end, day in pending:
        fetched = _guard(
            speak,
            secrets,
            lambda start=start, finish=end: client.records(start, finish, symbol, schema),
        )
        rows = []
        for raw in fetched:
            row = normalize_tick_row(raw, schema)
            if row is not None and row["ts_event"].date() == day:
                rows.append(row)
        path = write_tick_day(directory, day, rows, schema)
        saved += len(rows)
        speak(f"{day.isoformat()}: {len(rows)} rows in {path}.")
    speak(f"Done. {saved} new rows under {directory}.")
    speak("This command cannot place, change, or cancel an order.")
    return saved


def _download_span_ticks(client, secrets, speak, symbol, schema, pending, spans, directory: Path) -> int:
    saved = 0
    by_span: dict[tuple[datetime, datetime], list[tuple[datetime, datetime, date]]] = {span: [] for span in spans}
    for start, end, day in pending:
        for span in spans:
            if start >= span[0] and end <= span[1]:
                by_span[span].append((start, end, day))
                break
    for span, days in by_span.items():
        pending_rows = {day: [] for _start, _end, day in days}
        covered = span[0]
        for chunk_start, chunk_end in month_slices(span[0], span[1]):
            fetched = _guard(
                speak,
                secrets,
                lambda start=chunk_start, finish=chunk_end: client.records(start, finish, symbol, schema),
            )
            for raw in fetched:
                row = normalize_tick_row(raw, schema)
                if row is None:
                    continue
                day = row["ts_event"].date()
                if day in pending_rows:
                    pending_rows[day].append(row)
            covered = chunk_end
            for day in [day for day in list(pending_rows) if _day_ready(day, covered, span[1])]:
                path = write_tick_day(directory, day, pending_rows.pop(day), schema)
                count = _rows_in_file(path)
                saved += count
                speak(f"{day.isoformat()}: {count} rows in {path}.")
        for day, rows in list(pending_rows.items()):
            path = write_tick_day(directory, day, rows, schema)
            pending_rows.pop(day)
            saved += len(rows)
            speak(f"{day.isoformat()}: {len(rows)} rows in {path}.")
    speak(f"Done. {saved} new rows under {directory}.")
    speak("This command cannot place, change, or cancel an order.")
    return saved


def _day_ready(day: date, covered: datetime, span_end: datetime) -> bool:
    day_end = datetime(day.year, day.month, day.day, tzinfo=CHICAGO) + timedelta(days=1)
    return covered >= min(day_end.astimezone(timezone.utc), span_end)


def _count_tick_rows(directory: Path, days: list[date]) -> int:
    total = 0
    for day in days:
        path = day_file(directory, day)
        if path is not None:
            total += _rows_in_file(path)
    return total


def _rows_in_file(path: Path) -> int:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            return max(sum(1 for _line in handle) - 1, 0)
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return 0
    return int(pq.read_metadata(path).num_rows)


def _write_csv_gz(path: Path, rows: list[dict], schema: str) -> None:
    columns = TICK_COLUMNS[schema]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_cell(row.get(column)) for column in columns})


def _write_parquet(path: Path, rows: list[dict], schema: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    columns = TICK_COLUMNS[schema]
    arrays = {column: [_parquet_value(column, row.get(column)) for row in rows] for column in columns}
    types = {
        "ts_event": pa.timestamp("us", tz="America/Chicago"),
        "price": pa.float64(),
        "size": pa.int64(),
        "side": pa.string(),
        "action": pa.string(),
        "bid_px": pa.float64(),
        "ask_px": pa.float64(),
        "bid_sz": pa.int64(),
        "ask_sz": pa.int64(),
        "contract": pa.string(),
    }
    table = pa.table(arrays, schema=pa.schema([(column, types[column]) for column in columns]))
    pq.write_table(table, path, compression="zstd")


def _parquet_available() -> bool:
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except ImportError:
        return False
    return True


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(CHICAGO).isoformat(timespec="microseconds")
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _parquet_value(column: str, value: object):
    if column == "ts_event" and isinstance(value, datetime):
        return value.astimezone(CHICAGO)
    return value


def _symbol_root(symbol: str) -> str:
    root = symbol.split(".", 1)[0].strip().lower()
    cleaned = "".join(char for char in root if char.isalnum())
    return cleaned or "symbol"


def _side_code(value: object) -> str | None:
    """Databento side. A is a sell aggressor, B is a buy aggressor, N has no side."""
    if value is None:
        return "N"
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    text = str(value).strip()
    if text in {"A", "a", "Ask", "ASK"}:
        return "A"
    if text in {"B", "b", "Bid", "BID"}:
        return "B"
    if text in {"N", "n", "None", "NONE", "nan", "NaN", ""}:
        return "N"
    return None


def _action_code(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    text = str(value).strip()
    names = {
        "A": "A",
        "Add": "A",
        "M": "M",
        "Modify": "M",
        "C": "C",
        "Cancel": "C",
        "T": "T",
        "Trade": "T",
        "F": "F",
        "Fill": "F",
        "R": "R",
        "Clear": "R",
        "N": "N",
        "None": "N",
    }
    if text in names:
        return names[text]
    if len(text) == 1 and text.isalpha():
        return text.upper()
    return ""


def _whole_size(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0 or number == float("inf"):
        return None
    return int(number)


def _as_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_dataset_bounds(body: object, schema: str = SCHEMA) -> tuple[datetime | None, datetime | None]:
    if not isinstance(body, dict):
        return (None, None)
    start = body.get("start")
    end = body.get("end")
    nested = body.get("schema")
    if isinstance(nested, dict):
        slot = nested.get(schema)
        if isinstance(slot, dict):
            start = slot.get("start") or start
            end = slot.get("end") or end
    return (_parse_utc(start), _parse_utc(end))


class DatabentoGateway:
    """Official client. Tests pass a fake with the same methods instead."""

    def __init__(self, historical, api_key: str) -> None:
        self._hist = historical
        self.api_key = api_key

    def __repr__(self) -> str:
        return "DatabentoGateway(key=redacted)"

    def dataset_bounds(self, schema: str = SCHEMA) -> tuple[datetime | None, datetime | None]:
        body = self._hist.metadata.get_dataset_range(DATASET)
        return parse_dataset_bounds(body, schema)

    def cost(self, start: datetime, end: datetime, symbol: str, schema: str = SCHEMA) -> float:
        value = self._hist.metadata.get_cost(
            dataset=DATASET,
            start=start,
            end=end,
            symbols=symbol,
            schema=schema,
            stype_in=STYPE_IN,
        )
        return float(value)

    def bars(self, start: datetime, end: datetime, symbol: str, schema: str = SCHEMA) -> list[tuple]:
        store = self._hist.timeseries.get_range(
            dataset=DATASET,
            start=start,
            end=end,
            symbols=symbol,
            schema=schema,
            stype_in=STYPE_IN,
            stype_out="instrument_id",
        )
        frame = store.to_df(price_type="float", pretty_ts=True, map_symbols=False, tz="UTC")
        resolved = self._resolve_raw(store, frame, start, end)
        rows = []
        for record in frame.itertuples(index=True):
            moment = record[0]
            if hasattr(moment, "to_pydatetime"):
                moment = moment.to_pydatetime()
            instrument = getattr(record, "instrument_id", "")
            raw = raw_symbol_on(resolved, instrument, moment if isinstance(moment, datetime) else start)
            rows.append(
                (
                    moment,
                    getattr(record, "open"),
                    getattr(record, "high"),
                    getattr(record, "low"),
                    getattr(record, "close"),
                    getattr(record, "volume"),
                    instrument,
                    raw,
                )
            )
        return rows

    def records(self, start: datetime, end: datetime, symbol: str, schema: str = SCHEMA) -> list[dict]:
        """Tick rows for ``trades``, ``tbbo``, or ``mbp-1``. Prices are still raw until normalize."""
        store = self._hist.timeseries.get_range(
            dataset=DATASET,
            start=start,
            end=end,
            symbols=symbol,
            schema=schema,
            stype_in=STYPE_IN,
            stype_out="instrument_id",
        )
        frame = store.to_df(price_type="float", pretty_ts=True, map_symbols=False, tz="UTC")
        resolved = self._resolve_raw(store, frame, start, end)
        rows = []
        for record in frame.itertuples(index=False):
            moment = getattr(record, "ts_event", None)
            if hasattr(moment, "to_pydatetime"):
                moment = moment.to_pydatetime()
            instrument = getattr(record, "instrument_id", "")
            raw = raw_symbol_on(resolved, instrument, moment if isinstance(moment, datetime) else start)
            row = {
                "ts_event": moment,
                "price": getattr(record, "price", None),
                "size": getattr(record, "size", None),
                "side": getattr(record, "side", None),
                "instrument_id": instrument,
                "raw_symbol": raw,
            }
            if schema in {"tbbo", "mbp-1"}:
                row["bid_px"] = _field(record, "bid_px_00", "bid_px")
                row["ask_px"] = _field(record, "ask_px_00", "ask_px")
                row["bid_sz"] = _field(record, "bid_sz_00", "bid_sz")
                row["ask_sz"] = _field(record, "ask_sz_00", "ask_sz")
            if schema == "mbp-1":
                row["action"] = getattr(record, "action", None)
            rows.append(row)
        return rows

    def _resolve_raw(self, store, frame, start: datetime, end: datetime) -> dict:
        ids: list[str] = []
        mappings = getattr(store, "mappings", None) or {}
        if isinstance(mappings, dict):
            for intervals in mappings.values():
                for interval in intervals or []:
                    if isinstance(interval, dict):
                        ident = str(interval.get("symbol") or interval.get("s") or "").strip()
                        if ident:
                            ids.append(ident)
        columns = getattr(frame, "columns", [])
        if "instrument_id" in columns:
            for ident in frame["instrument_id"].unique():
                text = _instrument_text(ident)
                if text:
                    ids.append(text)
        unique = list(dict.fromkeys(ids))
        merged: dict = {"result": {}}
        if not unique:
            return merged
        start_date = start.astimezone(timezone.utc).date()
        end_date = _exclusive_date(end)
        for offset in range(0, len(unique), 2000):
            chunk = unique[offset : offset + 2000]
            body = self._hist.symbology.resolve(
                dataset=DATASET,
                symbols=chunk,
                stype_in="instrument_id",
                stype_out="raw_symbol",
                start_date=start_date,
                end_date=end_date,
            )
            result = body.get("result") if isinstance(body, dict) else None
            if isinstance(result, dict):
                merged["result"].update(result)
        return merged


def _client_from_env() -> DatabentoGateway:
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set DATABENTO_API_KEY in .env. The value is not printed.")
    try:
        import databento as db
    except ImportError:
        raise SystemExit(
            "The databento package is not installed. From the repo root run "
            "python -m pip install -r requirements.txt. The API key is not printed."
        ) from None
    try:
        historical = db.Historical(api_key)
    except Exception as exc:
        cleaned = _clean(str(exc), [api_key])
        if cleaned and api_key not in cleaned:
            raise SystemExit(
                "Databento did not accept the API key. The value is not printed. " + cleaned
            ) from None
        raise SystemExit("Databento did not accept the API key. The value is not printed.") from None
    return DatabentoGateway(historical, api_key)


def _guard(speak, secrets: list[str], fn):
    try:
        return fn()
    except SystemExit:
        raise
    except Exception as exc:
        message = _clean(str(exc), secrets)
        speak(message)
        raise SystemExit(message) from None


def _secrets(client: object) -> list[str]:
    found = []
    key = getattr(client, "api_key", None)
    if isinstance(key, str) and key.strip():
        found.append(key.strip())
    env = os.environ.get("DATABENTO_API_KEY", "").strip()
    if env and env not in found:
        found.append(env)
    return found


def _clean(text: str, secrets: list[str]) -> str:
    cleaned = redact_text(text, secrets)
    for secret in secrets:
        if secret and secret in cleaned:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


def _pair(bounds) -> tuple[datetime | None, datetime | None]:
    if not isinstance(bounds, tuple) or len(bounds) != 2:
        return (None, None)
    start, end = bounds
    return (
        start if isinstance(start, datetime) else None,
        end if isinstance(end, datetime) else None,
    )


def _parse_user_time(text: str) -> datetime:
    moment = _parse_utc(text)
    if moment is None:
        raise SystemExit(f"Could not read the date {text!r}. Nothing was downloaded.")
    return moment


def _default_end(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    day = now.astimezone(timezone.utc).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _exclusive_date(moment: datetime) -> date:
    moment = moment.astimezone(timezone.utc)
    if moment.hour == moment.minute == moment.second == moment.microsecond == 0:
        return moment.date()
    return moment.date() + timedelta(days=1)


def _parse_utc(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, tail = text.split(".", 1)
        digits = []
        rest_at = 0
        for index, char in enumerate(tail):
            if char.isdigit():
                digits.append(char)
                rest_at = index + 1
            else:
                rest_at = index
                break
        else:
            rest_at = len(tail)
        frac = "".join(digits)[:6]
        text = head + (("." + frac) if frac else "") + tail[rest_at:]
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    moment = _parse_utc(value)
    if moment is None:
        return None
    return moment.date()


def _field(record, *names):
    for name in names:
        if hasattr(record, name):
            value = getattr(record, name)
            if value is not None:
                return value
    return None


def _instrument_text(instrument_id: object) -> str:
    if instrument_id is None:
        return ""
    if isinstance(instrument_id, bool):
        return ""
    if isinstance(instrument_id, int):
        return str(instrument_id)
    if isinstance(instrument_id, float):
        if instrument_id != instrument_id:
            return ""
        if instrument_id == int(instrument_id):
            return str(int(instrument_id))
    text = str(instrument_id).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text

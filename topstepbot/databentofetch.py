"""Download long MES 1-minute history from Databento.

The symbol is ``MES.v.0``: continuous front month, ranked by the previous
day's volume (Databento roll rule ``v``, rank 0). ``MES.c.0`` is the calendar
rule and can stay on a quiet expiry after volume has moved, so it is not used.
``MES.n.0`` would rank by open interest.

Dataset ``GLBX.MDP3``, schema ``ohlcv-1m``, ``stype_in=continuous``. A
continuous symbol can only be resolved to ``instrument_id``. The outright
symbol (``MESU4``) comes from a free ``symbology.resolve`` of those ids.
Instrument ids are only unique within a day, so the CSV contract column is
``MESU4:instrument_id``.

Prices are the original outright prints. Databento does not back-adjust.
A roll is a real price gap. ``history-report`` starts indicators and the
opening range over when the contract column changes.

``metadata.get_cost`` runs before any ``timeseries.get_range``. The download
stops when that price is above ``--max-cost`` unless ``--yes`` is passed.
The API key is read from ``DATABENTO_API_KEY`` and is never printed.

The official ``databento`` package is used. A pip dry-run for CPython 3.14
on macOS (arm64 and x86_64) resolves ``databento`` 0.87.0 with
``databento-dbn`` 0.70.0, pandas, numpy, and pyarrow (checked 2026-10-09).
Python 3.14 support started in databento 0.68.0.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from topstepbot.historyfetch import _read_csv, _write_csv
from topstepbot.redact import redact_text
from topstepbot.timeutil import CHICAGO

DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
SYMBOL = "MES.v.0"
STYPE_IN = "continuous"
# Databento fixed-point prices use 1e-9. A real MES print is far below this.
_FIXED_POINT = 1_000_000_000
_PRICE_CEILING = 100_000.0
DEFAULT_MAX_COST = 20.0


def fetch_databento(
    *,
    start: str,
    out: str | Path = "data/mes_1m_databento.csv",
    end: str | None = None,
    max_cost: float = DEFAULT_MAX_COST,
    yes: bool = False,
    symbol: str = SYMBOL,
    client=None,
    say=print,
    now: datetime | None = None,
) -> int:
    """Write a resume-safe CSV. Returns how many bars are in the file.

    ``start`` and ``end`` are UTC. A plain date is midnight UTC. ``end`` is
    exclusive. The CSV timestamps are Central time.
    """
    if max_cost < 0:
        raise SystemExit("max-cost cannot be negative. Nothing was downloaded.")
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
        f"Requesting {symbol} as a continuous symbol on {DATASET}, schema {SCHEMA}. "
        "The v roll rule is the front month by the previous day's volume. "
        "The c rule follows the calendar expiry and can linger on a quiet month, so it is not used. "
        "The n rule ranks by open interest. "
        "Prices are the original outright prints. Databento does not back-adjust them. "
        "The contract column stores the outright symbol and the instrument id, so a roll is a new name and a price gap. "
        "An instrument id is only unique within one day, which is why both are written."
    )
    try:
        bounds = client.dataset_bounds()
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
                f"{DATASET} {SCHEMA} is available from {avail_start.isoformat()} UTC, "
                f"which is after {window_start.isoformat()} UTC. The download starts at the available time."
            )
            window_start = avail_start
        if avail_end is not None and window_end > avail_end:
            speak(
                f"{DATASET} {SCHEMA} is available through {avail_end.isoformat()} UTC. "
                "The download stops there."
            )
            window_end = avail_end
    if window_start >= window_end:
        raise SystemExit("The requested window is outside the dataset range. Nothing was downloaded.")

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

    cost = _guard(
        speak,
        secrets,
        lambda: client.cost(window_start, window_end, symbol),
    )
    try:
        cost = float(cost)
    except (TypeError, ValueError):
        speak("Databento did not return a numeric cost. Nothing was downloaded.")
        raise SystemExit("Databento did not return a numeric cost. Nothing was downloaded.") from None
    if cost != cost or cost < 0 or cost == float("inf"):
        speak("Databento did not return a usable cost. Nothing was downloaded.")
        raise SystemExit("Databento did not return a usable cost. Nothing was downloaded.") from None
    price = usd_text(cost)
    cap = usd_text(max_cost)
    speak(
        f"The exact cost is {price} US dollars. "
        f"That is the price of {symbol} {SCHEMA} on {DATASET} from {window_start.isoformat()} UTC "
        f"up to but not including {window_end.isoformat()} UTC, before any bars are downloaded. "
        f"The cap is {cap} US dollars."
    )
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
            lambda start=chunk_start, finish=chunk_end: client.bars(start, finish, symbol),
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
    """Official client. Tests pass a fake with the same three methods instead."""

    def __init__(self, historical, api_key: str) -> None:
        self._hist = historical
        self.api_key = api_key

    def __repr__(self) -> str:
        return "DatabentoGateway(key=redacted)"

    def dataset_bounds(self) -> tuple[datetime | None, datetime | None]:
        body = self._hist.metadata.get_dataset_range(DATASET)
        return parse_dataset_bounds(body, SCHEMA)

    def cost(self, start: datetime, end: datetime, symbol: str) -> float:
        value = self._hist.metadata.get_cost(
            dataset=DATASET,
            start=start,
            end=end,
            symbols=symbol,
            schema=SCHEMA,
            stype_in=STYPE_IN,
        )
        return float(value)

    def bars(self, start: datetime, end: datetime, symbol: str) -> list[tuple]:
        store = self._hist.timeseries.get_range(
            dataset=DATASET,
            start=start,
            end=end,
            symbols=symbol,
            schema=SCHEMA,
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

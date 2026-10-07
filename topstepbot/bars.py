"""1-minute CSV bars and aggregation up to the signal timeframe."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from topstepbot.models import Bar


def load_bars(path: str | Path, tz_name: str = "America/Chicago") -> list[Bar]:
    """Load OHLCV bars.

    Expected columns: timestamp, open, high, low, close, volume.
    Timestamps are ISO-8601. A trailing Z means UTC. A naive timestamp is
    read as `tz_name` (Central time by default). Lines starting with # are
    comments. Bars are returned oldest first.
    """
    tz = ZoneInfo(tz_name)
    bars: list[Bar] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if reader.fieldnames is None or not required.issubset({name.strip().lower() for name in reader.fieldnames}):
        raise ValueError(
            "CSV must have columns timestamp,open,high,low,close,volume. "
            f"Found {reader.fieldnames}."
        )
    # Normalize header case.
    for row in reader:
        lowered = {key.strip().lower(): value for key, value in row.items() if key}
        moment = _parse_time(lowered["timestamp"], tz)
        bars.append(
            Bar(
                time=moment,
                open=float(lowered["open"]),
                high=float(lowered["high"]),
                low=float(lowered["low"]),
                close=float(lowered["close"]),
                volume=float(lowered["volume"]),
            )
        )
    bars.sort(key=lambda bar: bar.time)
    return bars


def write_bars(path: str | Path, bars: list[Bar], comment: str | None = None) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        if comment:
            handle.write(f"# {comment}\n")
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for bar in bars:
            writer.writerow(
                [
                    bar.time.isoformat(),
                    f"{bar.open:.4f}",
                    f"{bar.high:.4f}",
                    f"{bar.low:.4f}",
                    f"{bar.close:.4f}",
                    f"{bar.volume:.0f}",
                ]
            )


def _parse_time(text: str, tz: ZoneInfo) -> datetime:
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment


class BarAggregator:
    """Build N-minute bars from 1-minute bars, aligned to the clock in `tz_name`."""

    def __init__(self, minutes: int, tz_name: str) -> None:
        if minutes < 1:
            raise ValueError("minutes must be positive")
        self.minutes = minutes
        self.tz = ZoneInfo(tz_name)
        self._bucket: datetime | None = None
        self._parts: list[Bar] = []

    def add(self, bar: Bar) -> Bar | None:
        start = self._bucket_start(bar.time)
        if self._bucket is None:
            self._bucket = start
            self._parts = [bar]
            return None
        if start != self._bucket:
            finished = self._finish()
            self._bucket = start
            self._parts = [bar]
            return finished
        self._parts.append(bar)
        return None

    def flush(self) -> Bar | None:
        if not self._parts:
            return None
        finished = self._finish()
        self._bucket = None
        self._parts = []
        return finished

    def _bucket_start(self, moment: datetime) -> datetime:
        local = moment.astimezone(self.tz)
        minute = (local.minute // self.minutes) * self.minutes
        return local.replace(minute=minute, second=0, microsecond=0)

    def _finish(self) -> Bar:
        parts = self._parts
        assert self._bucket is not None
        return Bar(
            time=self._bucket,
            open=parts[0].open,
            high=max(part.high for part in parts),
            low=min(part.low for part in parts),
            close=parts[-1].close,
            volume=sum(part.volume for part in parts),
        )


def bar_close_time(bar: Bar, minutes: int) -> datetime:
    return bar.time + timedelta(minutes=minutes)

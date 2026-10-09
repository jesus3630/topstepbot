"""Daily log of signals, orders, fills, and P&L. Secrets never go here."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from topstepbot.board import console_text
from topstepbot.timeutil import CHICAGO


class Journal:
    def __init__(self, log_dir: str | Path, clock: datetime | None = None) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        day = (clock or datetime.now(CHICAGO)).astimezone(CHICAGO).date().isoformat()
        self.path = self.log_dir / f"bot-{day}.log"
        self.logger = logging.getLogger(f"topstepbot.journal.{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.sink = None
        if not self.logger.handlers:
            file_handler = logging.FileHandler(self.path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            stream = logging.StreamHandler()
            stream.setFormatter(_ConsoleFormatter())
            self.logger.addHandler(file_handler)
            self.logger.addHandler(stream)

    def event(self, kind: str, **fields: object) -> None:
        parts = [kind]
        for key, value in fields.items():
            if _secret_key(key):
                continue
            parts.append(f"{key}={value}")
        message = " ".join(parts)
        self.logger.info(message)
        self._mirror("info", message)

    def signal(self, **fields: object) -> None:
        self.event("SIGNAL", **fields)

    def skip(self, **fields: object) -> None:
        self.event("SKIP", **fields)

    def order(self, **fields: object) -> None:
        self.event("ORDER", **fields)

    def fill(self, **fields: object) -> None:
        self.event("FILL", **fields)

    def pnl(self, **fields: object) -> None:
        self.event("PNL", **fields)

    def info(self, message: str) -> None:
        self.logger.info(message)
        self._mirror("info", message)

    def heartbeat(self, message: str) -> None:
        """Terminal and log file only. The dashboard event list stays for trades."""
        self.logger.info(message)

    def error(self, message: str) -> None:
        self.logger.error(message)
        self._mirror("error", message)

    def _mirror(self, level: str, message: str) -> None:
        sink = self.sink
        if sink is None:
            return
        try:
            sink(level, message)
        except Exception:
            return


class _ConsoleFormatter(logging.Formatter):
    """Short sentences on the terminal. The log file keeps the detailed line."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.now(CHICAGO).strftime("%H:%M:%S")
        level = "ERROR" if record.levelno >= logging.ERROR else "INFO"
        return f"{stamp}  {console_text(record.getMessage(), level)}"


def _secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in ("apikey", "api_key", "token", "password", "secret", "authorization"))

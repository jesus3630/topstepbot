"""Daily log of signals, orders, fills, and P&L. Secrets never go here."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

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
        if not self.logger.handlers:
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            file_handler = logging.FileHandler(self.path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            stream = logging.StreamHandler()
            stream.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            self.logger.addHandler(stream)

    def event(self, kind: str, **fields: object) -> None:
        parts = [kind]
        for key, value in fields.items():
            if _secret_key(key):
                continue
            parts.append(f"{key}={value}")
        self.logger.info(" ".join(parts))

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

    def error(self, message: str) -> None:
        self.logger.error(message)


def _secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in ("apikey", "api_key", "token", "password", "secret", "authorization"))

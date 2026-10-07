"""Kill switch: a file flag or an explicit trip cancels orders and flattens.

The live loop also treats the typed command "kill" and Ctrl+C as a trip.
Those call `trip`. This object is the single flag the rest of the bot checks.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from topstepbot.journal import Journal


class KillSwitch:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.tripped = False
        self.reason = ""

    def trip(self, reason: str) -> None:
        if self.tripped:
            return
        self.tripped = True
        self.reason = reason
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(f"{reason}\n", encoding="utf-8")
        except OSError:
            # The in-memory flag still stops trading if the disk write fails.
            pass

    def check(self) -> bool:
        if self.tripped:
            return True
        if self.path.exists():
            self.tripped = True
            try:
                text = self.path.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            self.reason = text or "kill file present"
            return True
        return False

    def execute(self, broker, price: float, when: datetime, journal: Journal) -> bool:
        """Cancel working orders and flatten if the switch is tripped.

        Returns True when it fired. Safe to call every loop; it does nothing
        until the file exists or `trip` was called.
        """
        if not self.check():
            return False
        journal.event("KILL", reason=self.reason or "kill switch")
        broker.cancel_all()
        fills = broker.flatten(price, when)
        for fill in fills:
            journal.fill(
                order=fill.order_id,
                role=fill.role,
                qty=fill.qty,
                price=f"{fill.price:.4f}",
                reason="kill switch",
            )
        journal.event("KILL", state="flat", net_qty=broker.net_qty())
        return True

    def clear_file(self) -> None:
        """Remove a leftover kill file before a new supervised run.

        Does not clear an in-memory trip. Call this only at startup, before
        the session, when the trader has confirmed they want to run again.
        """
        if self.path.exists() and not self.tripped:
            try:
                self.path.unlink()
            except OSError:
                pass

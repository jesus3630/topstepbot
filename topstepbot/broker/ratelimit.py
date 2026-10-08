"""ProjectX request pacing and folded error logs.

The gateway documents two buckets (https://gateway.docs.projectx.com/docs/getting-started/rate-limits/):

- POST /api/History/retrieveBars: 50 requests per 30 seconds
- every other endpoint: 200 requests per 60 seconds

HTTP 429 means wait and retry. The docs do not define a Retry-After header;
this limiter honors a numeric Retry-After when the gateway sends one.
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Callable

HISTORY_PATH = "/api/History/retrieveBars"
HISTORY_LIMIT = (50, 30.0)
OTHER_LIMIT = (200, 60.0)


class RateHalt(RuntimeError):
    """The limiter refused another request. The client turns this into ApiHalted."""


class ErrorFold:
    """Emit the first copy of an error immediately and count the rest.

    ``flush`` writes one summary line. A tight loop of the same message
    therefore produces two lines, not one per attempt.
    """

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._last: str | None = None
        self._extra = 0

    def report(self, message: str) -> None:
        if message == self._last:
            self._extra += 1
            return
        self.flush()
        self._emit(message)
        self._last = message
        self._extra = 0

    def flush(self) -> None:
        if self._last is not None and self._extra:
            self._emit(f"{self._last} (repeated {self._extra} more times)")
            self._extra = 0


class SlidingWindowLimiter:
    """Wait until a request fits the bucket. Never busy-spin."""

    def __init__(
        self,
        *,
        history_limit: tuple[int, float] = HISTORY_LIMIT,
        other_limit: tuple[int, float] = OTHER_LIMIT,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        jitter: Callable[[], float] | None = None,
        max_consecutive: int = 5,
        failure_window: float = 30.0,
    ) -> None:
        self.history_limit = history_limit
        self.other_limit = other_limit
        self.clock = clock
        self.sleep = sleep
        self.jitter = jitter or (lambda: random.uniform(0.0, 0.25))
        self.max_consecutive = max_consecutive
        self.failure_window = failure_window
        self._history: deque[float] = deque()
        self._other: deque[float] = deque()
        self.consecutive_failures = 0
        self.failure_started_at: float | None = None
        self.halted = False
        self.halt_reason = ""
        self.probe_budget = 0

    def before_request(self, path: str) -> None:
        if self.halted:
            raise RateHalt(self.halt_reason or "ProjectX API halted. Trading stopped.")
        queue, limit, window = self._bucket(path)
        now = self.clock()
        self._drop_expired(queue, now, window)
        if len(queue) >= limit:
            wait = window - (now - queue[0])
            if wait > 0:
                self.sleep(wait)
            now = self.clock()
            self._drop_expired(queue, now, window)
            if len(queue) >= limit:
                self._halt("Rate limiter clock is not advancing. Trading stopped.")
        queue.append(self.clock())

    def note_success(self) -> None:
        self.consecutive_failures = 0
        self.failure_started_at = None

    def note_failure(self, retry_after: float | None) -> float:
        """Return how long to wait before a safe retry. Raises RateHalt when the budget is spent."""
        now = self.clock()
        if self.failure_started_at is None:
            self.failure_started_at = now
        self.consecutive_failures += 1
        elapsed = now - self.failure_started_at
        if self.consecutive_failures >= self.max_consecutive or elapsed >= self.failure_window:
            self._halt(
                "ProjectX API halted after "
                f"{self.consecutive_failures} consecutive failures "
                f"({elapsed:.0f}s). Trading stopped."
            )
        base = min(30.0, float(2 ** (self.consecutive_failures - 1)))
        delay = base if retry_after is None else max(base, float(retry_after))
        return delay + max(0.0, float(self.jitter()))

    def allow_halt_probe(self, count: int = 2) -> None:
        """Permit a few read calls after a halt so the book can be printed once."""
        self.probe_budget = max(0, int(count))

    def consume_probe(self) -> bool:
        if self.probe_budget <= 0:
            return False
        self.probe_budget -= 1
        return True

    def _halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        self.probe_budget = 0
        raise RateHalt(reason)

    def _bucket(self, path: str) -> tuple[deque[float], int, float]:
        if path == HISTORY_PATH:
            limit, window = self.history_limit
            return self._history, int(limit), float(window)
        limit, window = self.other_limit
        return self._other, int(limit), float(window)

    @staticmethod
    def _drop_expired(queue: deque[float], now: float, window: float) -> None:
        while queue and now - queue[0] >= window:
            queue.popleft()

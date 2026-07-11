"""In-process sliding-window rate limiter.

Generic per-identity call budget over a fixed window. Used by the AI diagnosis
POST endpoint to bound LLM spend; state is process-local (same single-instance
caveat as LoginThrottle). Clock is injected so expiry is deterministic in tests.
"""

from __future__ import annotations

import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qarunner.errors import RateLimited

if TYPE_CHECKING:
    from qarunner.ports.clock import Clock


@dataclass
class SlidingWindowRateLimiter:
    """Allow at most ``max_calls`` hits per identity inside ``window_seconds``.

    ``max_calls <= 0`` disables the limiter (every call is allowed). When a call
    would exceed the budget, raises :class:`RateLimited` with a whole-second
    ``retry_after`` equal to how long until the oldest hit in the window ages out.
    """

    clock: Clock
    max_calls: int = 10
    window_seconds: float = 60.0
    max_identities: int = 10000
    _hits: OrderedDict[str, deque[float]] = field(default_factory=OrderedDict)

    def check_and_record(self, key: str) -> None:
        """Record one call for *key*, or raise :class:`RateLimited` if over budget."""
        if self.max_calls <= 0:
            return

        now = self.clock.now().timestamp()
        cutoff = now - self.window_seconds
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_identities:
                self._evict_one()
            hits = deque()
            self._hits[key] = hits
        else:
            self._hits.move_to_end(key)

        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= self.max_calls:
            retry_after = max(1, math.ceil(hits[0] + self.window_seconds - now))
            raise RateLimited(retry_after)

        hits.append(now)

    def _evict_one(self) -> None:
        """Drop the least-recently-used identity to stay under the cap."""
        self._hits.popitem(last=False)

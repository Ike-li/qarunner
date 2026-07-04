"""In-process login brute-force throttle (SEC-5).

Tracks consecutive failed logins per identity (the caller keys on
``username|client-ip``) and locks that identity for an exponentially growing
window once a threshold of failures is reached. State lives in this process
only — like crash recovery it assumes a single instance; a multi-replica
deployment would track each replica independently (documented caveat). The
clock is injected so lock expiry is deterministic under test.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qarunner.errors import LoginLockedOut

if TYPE_CHECKING:
    from qarunner.ports.clock import Clock


@dataclass
class _Attempt:
    """Per-identity counters; ``locked_until`` is epoch seconds (0 = unlocked)."""

    failures: int = 0
    lockouts: int = 0
    locked_until: float = 0.0


@dataclass
class LoginThrottle:
    """Exponential-backoff lockout for repeated failed logins.

    After ``threshold`` consecutive failures an identity is locked for
    ``base_seconds``, doubling for each further lockout up to ``max_seconds``. A
    successful login clears the identity; an expired lock resets the failure
    window but remembers prior lockouts so the backoff keeps escalating against
    a persistent attacker.
    """

    clock: Clock
    threshold: int = 5
    base_seconds: float = 60.0
    max_seconds: float = 900.0
    # Bounded number of tracked identities (memory-exhaustion DoS guard).
    max_identities: int = 10000
    _attempts: OrderedDict[str, _Attempt] = field(default_factory=OrderedDict)

    def check(self, key: str) -> None:
        """Raise ``LoginLockedOut`` if *key* is currently locked.

        A lock whose window has elapsed is cleared here (failure count reset,
        prior lockout count retained) so the next attempt starts fresh.
        """
        attempt = self._attempts.get(key)
        if attempt is None:
            return

        # Move key to end to mark as recently used
        self._attempts.move_to_end(key)

        if attempt.locked_until == 0.0:
            return
        remaining = attempt.locked_until - self.clock.now().timestamp()
        if remaining > 0:
            raise LoginLockedOut(max(1, math.ceil(remaining)))
        attempt.locked_until = 0.0
        attempt.failures = 0

    def record_failure(self, key: str) -> None:
        """Count a failed attempt for *key*, locking it once at the threshold."""
        attempt = self._attempts.get(key)
        if attempt is None:
            if len(self._attempts) >= self.max_identities:
                self._evict_one()
            attempt = _Attempt()
            self._attempts[key] = attempt
        else:
            self._attempts.move_to_end(key)

        attempt.failures += 1
        if self.threshold > 0 and attempt.failures >= self.threshold:
            window = min(self.base_seconds * (2**attempt.lockouts), self.max_seconds)
            attempt.locked_until = self.clock.now().timestamp() + window
            attempt.lockouts += 1
            attempt.failures = 0

    def record_success(self, key: str) -> None:
        """Clear all failure/lock state for *key* after a successful login."""
        self._attempts.pop(key, None)

    def _evict_one(self) -> None:
        """Drop one identity to stay under the cap, preferring one that is NOT
        currently locked — so a flood of fresh keys can't evict an active lockout
        (lock bypass). Falls back to the oldest only if every identity is locked."""
        now = self.clock.now().timestamp()
        victim = next(
            (k for k, a in self._attempts.items() if a.locked_until <= now), None
        )
        if victim is None:
            self._attempts.popitem(last=False)
        else:
            del self._attempts[victim]


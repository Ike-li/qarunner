"""Small PostgreSQL lifecycle Fakes for unit-of-work failure tests."""

from __future__ import annotations


class StartFailingTransaction:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def start(self) -> None:
        raise self.error


class StartFailingConnection:
    def __init__(self, error: Exception) -> None:
        self.transaction_value = StartFailingTransaction(error)

    def transaction(self, *, isolation: str) -> StartFailingTransaction:
        assert isolation == "read_committed"
        return self.transaction_value


class StartFailingPool:
    def __init__(self, error: Exception) -> None:
        self.connection = StartFailingConnection(error)
        self.acquires = 0
        self.releases = 0

    async def acquire(self) -> StartFailingConnection:
        self.acquires += 1
        return self.connection

    async def release(self, connection: StartFailingConnection) -> None:
        assert connection is self.connection
        self.releases += 1

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


class RecordingTransaction:
    def __init__(self) -> None:
        self.starts = 0
        self.commits = 0
        self.rollbacks = 0

    async def start(self) -> None:
        self.starts += 1

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class RecordingConnection:
    def __init__(self) -> None:
        self.transaction_value = RecordingTransaction()

    def transaction(self, *, isolation: str) -> RecordingTransaction:
        assert isolation == "read_committed"
        return self.transaction_value


class RecordingPool:
    def __init__(self) -> None:
        self.connection = RecordingConnection()
        self.acquires = 0
        self.releases = 0

    async def acquire(self) -> RecordingConnection:
        self.acquires += 1
        return self.connection

    async def release(self, connection: RecordingConnection) -> None:
        assert connection is self.connection
        self.releases += 1

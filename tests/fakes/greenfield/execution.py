"""Deterministic execution-coordination Fakes."""

from qarunner.application.ports.clock import UtcClock
from qarunner.application.ports.common import ReplayResult
from qarunner.application.ports.execution import (
    ExecutionStopReceipt,
    ExecutionStopRequest,
)
from qarunner.domain.errors import IdempotencyConflict


class InMemoryExecutionStopControl:
    """Record accepted stop intents without contacting a Worker or runtime."""

    def __init__(
        self,
        clock: UtcClock,
        *,
        before_accept_error: Exception | None = None,
        after_accept_error: Exception | None = None,
    ) -> None:
        self.__clock = clock
        self.__before_accept_error = before_accept_error
        self.__after_accept_error = after_accept_error
        self.__accepted: list[ExecutionStopRequest] = []
        self.__receipts: dict[str, tuple[ExecutionStopRequest, ExecutionStopReceipt]] = {}

    async def request_stop(
        self, request: ExecutionStopRequest
    ) -> ReplayResult[ExecutionStopReceipt]:
        if self.__before_accept_error is not None:
            error = self.__before_accept_error
            self.__before_accept_error = None
            raise error
        stored = self.__receipts.get(request.request_id)
        if stored is not None:
            stored_request, stored_receipt = stored
            if stored_request.digest == request.digest:
                return ReplayResult(value=stored_receipt, replayed=True)
            raise IdempotencyConflict(
                scope="execution_stop",
                key=request.request_id,
                stored_digest=stored_request.digest,
                received_digest=request.digest,
            )
        receipt = ExecutionStopReceipt(
            request_id=request.request_id,
            request_digest=request.digest,
            recorded_at=self.__clock.now(),
        )
        self.__accepted.append(request)
        self.__receipts[request.request_id] = (request, receipt)
        if self.__after_accept_error is not None:
            error = self.__after_accept_error
            self.__after_accept_error = None
            raise error
        return ReplayResult(value=receipt, replayed=False)

    def accepted_requests(self) -> tuple[ExecutionStopRequest, ...]:
        return tuple(self.__accepted)

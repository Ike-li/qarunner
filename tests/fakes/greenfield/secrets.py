"""Fail-closed Secret broker Fakes."""

from qarunner.application.ports.clock import UtcClock
from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.secrets import SecretDeliveryLease, SecretRequest


class PresetSecretBroker:
    """Issue only explicitly seeded opaque delivery leases."""

    def __init__(
        self,
        clock: UtcClock,
        entries: tuple[tuple[SecretRequest, SecretDeliveryLease], ...],
    ) -> None:
        self.__clock = clock
        self.__entries = dict(entries)
        self.__issued: list[SecretRequest] = []
        self.__issued_handles: set[str] = set()
        self.__revoked_handles: set[str] = set()

    async def issue(self, request: SecretRequest) -> SecretDeliveryLease:
        lease = self.__entries.get(request)
        if lease is None:
            raise PortContractError(
                resource="secret_broker",
                field="request",
                reason="not_authorized",
            )
        if lease.delivery_handle in self.__revoked_handles:
            raise PortContractError(
                resource="secret_broker",
                field="lease",
                reason="revoked",
            )
        if lease.expires_at > request.requested_expires_at:
            raise PortContractError(
                resource="secret_broker",
                field="lease",
                reason="exceeds_requested_expiry",
            )
        if self.__clock.now() >= lease.expires_at:
            raise PortContractError(
                resource="secret_broker",
                field="lease",
                reason="expired",
            )
        self.__issued.append(request)
        self.__issued_handles.add(lease.delivery_handle)
        return lease

    async def revoke(self, delivery_handle: str) -> None:
        if delivery_handle in self.__revoked_handles:
            return
        if delivery_handle not in self.__issued_handles:
            raise PortContractError(
                resource="secret_broker",
                field="delivery_handle",
                reason="unknown",
            )
        self.__revoked_handles.add(delivery_handle)

    def issued_requests(self) -> tuple[SecretRequest, ...]:
        return tuple(self.__issued)


class DenyAllSecretBroker:
    """Reject every secret request unless a test explicitly chooses another Fake."""

    async def issue(self, request: SecretRequest) -> SecretDeliveryLease:
        raise PortContractError(
            resource="secret_broker",
            field="request",
            reason="not_authorized",
        )

    async def revoke(self, delivery_handle: str) -> None:
        raise PortContractError(
            resource="secret_broker",
            field="delivery_handle",
            reason="not_authorized",
        )

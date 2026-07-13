"""Fixed-sequence opaque ID Fake."""

from collections import deque

from qarunner.application.ports.common import PortContractError


class FixedSequenceIdGenerator:
    """Return only explicitly seeded IDs, in order."""

    def __init__(self, values: tuple[str, ...]) -> None:
        if any(not _is_seeded_opaque_id(value) for value in values):
            raise PortContractError(
                resource="id_generator",
                field="values",
                reason="not_opaque_id",
            )
        if len(set(values)) != len(values):
            raise PortContractError(
                resource="id_generator",
                field="values",
                reason="duplicate",
            )
        self.__remaining = deque(values)

    def new_id(self) -> str:
        if not self.__remaining:
            raise PortContractError(
                resource="id_generator",
                field="values",
                reason="exhausted",
            )
        return self.__remaining.popleft()


def _is_seeded_opaque_id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())

"""Contract surface for atomic policy retry publication."""

from qarunner.application import ports


def test_policy_retry_port_is_exported_as_one_atomic_gateway() -> None:
    assert ports.RunRetryGateway
    assert ports.RunRetryPublication
    assert ports.RunRetryWriteAuthority
    assert ports.RunRetryMutationSnapshot
    assert ports.RunRetryProjection
    assert ports.RetryBudgetReservation

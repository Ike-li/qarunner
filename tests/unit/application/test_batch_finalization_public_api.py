"""001H application handler facade remains stable for downstream adapters."""


def test_batch_finalization_handlers_are_exported_from_application_facade() -> None:
    from qarunner.application import (
        BeginBatchFinalization,
        BeginBatchFinalizationCommand,
        BeginBatchFinalizationResult,
        FinalizeBatch,
        FinalizeBatchCommand,
        FinalizeBatchResult,
    )

    exported = (
        BeginBatchFinalization,
        BeginBatchFinalizationCommand,
        BeginBatchFinalizationResult,
        FinalizeBatch,
        FinalizeBatchCommand,
        FinalizeBatchResult,
    )
    assert all(value.__module__.startswith("qarunner.application") for value in exported)

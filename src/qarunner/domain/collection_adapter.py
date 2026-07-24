"""Collection adapter acceptance for M2 (T-M2-ADAPTER-001 / T-M2-INPUT-001).

The control plane never imports or executes user test code. It only validates a
structured adapter result against frozen inputs and framework locator contracts,
then builds an immutable CaseManifest.
"""

from __future__ import annotations

from qarunner.domain.errors import CollectionAdapterRejected, DomainValidationError
from qarunner.domain.manifest import (
    CaseManifest,
    FrameworkLocator,
    ManifestInputs,
    ManifestItem,
)

_SUPPORTED_FRAMEWORKS = frozenset({"pytest", "playwright"})

_LOCATOR_KIND_BY_FRAMEWORK: dict[str, str] = {
    "pytest": "pytest_nodeid",
    "playwright": "playwright_test",
}

_REQUIRED_LOCATOR_PARTS: dict[str, frozenset[str]] = {
    "pytest_nodeid": frozenset({"file", "node"}),
    "playwright_test": frozenset({"project", "file", "title"}),
}

_CONTRACT_BY_FRAMEWORK: dict[str, str] = {
    "pytest": "qep.pytest-collection.v1",
    "playwright": "qep.playwright-collection.v1",
}


def accept_collection_adapter_result(
    *,
    framework: str,
    expected_inputs: ManifestInputs,
    claimed_inputs: ManifestInputs,
    items: tuple[ManifestItem, ...],
    manifest_id: str,
    batch_id: str,
) -> CaseManifest:
    """Accept a structured collection result or reject with a stable reason.

    This function is a pure data transform: it must not load user packages,
    spawn processes, or read the suite source tree.
    """
    if not isinstance(framework, str) or not framework.strip():
        raise CollectionAdapterRejected(
            reason="unsupported_framework", reason_class="invalid_input"
        )
    if framework not in _SUPPORTED_FRAMEWORKS:
        raise CollectionAdapterRejected(
            reason="unsupported_framework", reason_class="invalid_input"
        )
    if not isinstance(expected_inputs, ManifestInputs) or not isinstance(
        claimed_inputs, ManifestInputs
    ):
        raise CollectionAdapterRejected(
            reason="input_digest_mismatch", reason_class="integrity_failure"
        )
    if claimed_inputs != expected_inputs:
        raise CollectionAdapterRejected(
            reason="input_digest_mismatch", reason_class="integrity_failure"
        )
    expected_contract = _CONTRACT_BY_FRAMEWORK[framework]
    if claimed_inputs.collection_contract_version != expected_contract:
        raise CollectionAdapterRejected(
            reason="unsupported_collection_contract", reason_class="invalid_input"
        )
    if not items:
        raise CollectionAdapterRejected(reason="empty_manifest", reason_class="invalid_input")
    expected_kind = _LOCATOR_KIND_BY_FRAMEWORK[framework]
    for item in items:
        if not isinstance(item, ManifestItem):
            raise CollectionAdapterRejected(reason="malformed_item", reason_class="invalid_input")
        _require_locator_for_framework(item.framework_locator, expected_kind=expected_kind)
    try:
        return CaseManifest.create(
            manifest_id=manifest_id,
            batch_id=batch_id,
            inputs=claimed_inputs,
            items=items,
        )
    except DomainValidationError as error:
        raise CollectionAdapterRejected(
            reason="malformed_manifest", reason_class="invalid_input"
        ) from error


def _require_locator_for_framework(locator: FrameworkLocator, *, expected_kind: str) -> None:
    if not isinstance(locator, FrameworkLocator):
        raise CollectionAdapterRejected(reason="malformed_locator", reason_class="invalid_input")
    if locator.kind != expected_kind:
        raise CollectionAdapterRejected(
            reason="unsupported_locator_kind", reason_class="invalid_input"
        )
    required = _REQUIRED_LOCATOR_PARTS[expected_kind]
    present = {name for name, _value in locator.parts}
    if not required.issubset(present):
        raise CollectionAdapterRejected(
            reason="locator_parts_incomplete", reason_class="invalid_input"
        )

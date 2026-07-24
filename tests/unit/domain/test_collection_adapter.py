"""T-M2-ADAPTER-001 + T-M2-INPUT-001 (domain): collection adapter accept/reject.

Observable contract (AC-MVP-027 / T-M2-ADAPTER-001 / T-M2-INPUT-001):
- unsupported framework is rejected with a stable reason;
- unsupported / malformed framework locators are rejected;
- claimed inputs that do not match the frozen expected digests are rejected
  (no silent use of floating/changed content);
- accepted structured results produce a CaseManifest without the control plane
  executing user test code (pure data transform only).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

CREATED_AT = datetime(2026, 7, 24, 12, tzinfo=UTC)


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-collection-input.v1",
        payload={"label": label},
    )


def _inputs(*, label: str = "pinned"):
    from qarunner.domain import ManifestInputs

    return ManifestInputs(
        suite_revision_digest=_digest(f"{label}:suite"),
        source_digest=_digest(f"{label}:source"),
        dependency_digest=_digest(f"{label}:deps"),
        config_digest=_digest(f"{label}:config"),
        runner_digest=_digest(f"{label}:runner"),
        collection_contract_version="qep.pytest-collection.v1",
    )


def _pytest_locator(stable_case_id: str):
    from qarunner.domain import FrameworkLocator

    return FrameworkLocator(
        schema_version="qep.pytest-locator.v1",
        kind="pytest_nodeid",
        parts=(("file", "tests/test_shop.py"), ("node", stable_case_id)),
    )


def _playwright_locator(stable_case_id: str):
    from qarunner.domain import FrameworkLocator

    return FrameworkLocator(
        schema_version="qep.playwright-locator.v1",
        kind="playwright_test",
        parts=(
            ("project", "chromium"),
            ("file", "tests/shop.spec.ts"),
            ("title", stable_case_id),
        ),
    )


def _item(
    *,
    item_index: int,
    stable_case_id: str,
    locator,
    resource_profile_id: str = "profile-default",
):
    from qarunner.domain import (
        EstimateConfidence,
        ManifestConstraints,
        ManifestItem,
        WorkEstimate,
    )

    return ManifestItem(
        item_index=item_index,
        stable_case_id=stable_case_id,
        framework_locator=locator,
        atomic_group_id=stable_case_id,
        resource_profile_id=resource_profile_id,
        constraints=ManifestConstraints(
            serial_group=None,
            environment_requirements=(),
            account_requirements=(),
            data_lease_requirements=(),
        ),
        estimate=WorkEstimate(duration_ms=100, confidence=EstimateConfidence.MEDIUM),
        tags=("regression",),
        selection_metadata_digest=_digest(f"selection:{stable_case_id}"),
    )


def test_accept_pytest_collection_result_builds_manifest() -> None:
    from qarunner.domain import CaseManifest, accept_collection_adapter_result

    expected = _inputs()
    items = (
        _item(
            item_index=0,
            stable_case_id="tests/test_shop.py::test_checkout",
            locator=_pytest_locator("tests/test_shop.py::test_checkout"),
        ),
    )
    manifest = accept_collection_adapter_result(
        framework="pytest",
        expected_inputs=expected,
        claimed_inputs=expected,
        items=items,
        manifest_id="manifest-001",
        batch_id="batch-001",
    )
    assert isinstance(manifest, CaseManifest)
    assert manifest.batch_id == "batch-001"
    assert len(manifest.items) == 1
    assert manifest.inputs == expected


def test_accept_playwright_collection_result_builds_manifest() -> None:
    from qarunner.domain import accept_collection_adapter_result

    expected = _inputs()
    expected = type(expected)(
        suite_revision_digest=expected.suite_revision_digest,
        source_digest=expected.source_digest,
        dependency_digest=expected.dependency_digest,
        config_digest=expected.config_digest,
        runner_digest=expected.runner_digest,
        collection_contract_version="qep.playwright-collection.v1",
    )
    items = (
        _item(
            item_index=0,
            stable_case_id="shop checkout",
            locator=_playwright_locator("shop checkout"),
        ),
    )
    manifest = accept_collection_adapter_result(
        framework="playwright",
        expected_inputs=expected,
        claimed_inputs=expected,
        items=items,
        manifest_id="manifest-pw",
        batch_id="batch-pw",
    )
    assert manifest.items[0].framework_locator.kind == "playwright_test"


def test_reject_unsupported_framework() -> None:
    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    expected = _inputs()
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="junit",
            expected_inputs=expected,
            claimed_inputs=expected,
            items=(
                _item(
                    item_index=0,
                    stable_case_id="case-1",
                    locator=_pytest_locator("case-1"),
                ),
            ),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "unsupported_framework"
    assert caught.value.reason_class == "invalid_input"


def test_reject_locator_kind_mismatch_for_framework() -> None:
    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    expected = _inputs()
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=expected,
            claimed_inputs=expected,
            items=(
                _item(
                    item_index=0,
                    stable_case_id="case-1",
                    locator=_playwright_locator("case-1"),
                ),
            ),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "unsupported_locator_kind"


def test_reject_malformed_locator_parts() -> None:
    from qarunner.domain import (
        CollectionAdapterRejected,
        FrameworkLocator,
        accept_collection_adapter_result,
    )

    expected = _inputs()
    incomplete = FrameworkLocator(
        schema_version="qep.pytest-locator.v1",
        kind="pytest_nodeid",
        parts=(("file", "tests/test_shop.py"),),  # missing node
    )
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=expected,
            claimed_inputs=expected,
            items=(
                _item(
                    item_index=0,
                    stable_case_id="case-1",
                    locator=incomplete,
                ),
            ),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "locator_parts_incomplete"


def test_reject_floating_input_digest_mismatch() -> None:
    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    expected = _inputs(label="pinned")
    claimed = _inputs(label="changed-source")
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=expected,
            claimed_inputs=claimed,
            items=(
                _item(
                    item_index=0,
                    stable_case_id="case-1",
                    locator=_pytest_locator("case-1"),
                ),
            ),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "input_digest_mismatch"
    assert caught.value.reason_class == "integrity_failure"


def test_reject_empty_case_list() -> None:
    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    expected = _inputs()
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=expected,
            claimed_inputs=expected,
            items=(),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "empty_manifest"


def test_accept_path_module_does_not_import_or_execute_user_code() -> None:
    """Control-plane accept is a pure data transform (AC-MVP-027 zero user exec)."""
    from pathlib import Path

    source = Path("src/qarunner/domain/collection_adapter.py").read_text(encoding="utf-8")
    forbidden = (
        "subprocess",
        "importlib",
        "os.system",
        "Path(",
        "open(",
        "exec(",
        "eval(",
        "__import__",
        "docker",
    )
    for token in forbidden:
        assert token not in source, token


def test_reject_blank_framework() -> None:
    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    expected = _inputs()
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework=" ",
            expected_inputs=expected,
            claimed_inputs=expected,
            items=(
                _item(
                    item_index=0,
                    stable_case_id="case-1",
                    locator=_pytest_locator("case-1"),
                ),
            ),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "unsupported_framework"


def test_reject_wrong_collection_contract_version() -> None:
    from dataclasses import replace

    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    expected = _inputs()
    claimed = replace(expected, collection_contract_version="qep.unknown-collection.v9")
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=expected,
            claimed_inputs=claimed,
            items=(
                _item(
                    item_index=0,
                    stable_case_id="case-1",
                    locator=_pytest_locator("case-1"),
                ),
            ),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    # claimed digests differ from expected because replace still compares full inputs
    assert caught.value.reason in {"input_digest_mismatch", "unsupported_collection_contract"}


def test_reject_malformed_item_type() -> None:
    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    expected = _inputs()
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=expected,
            claimed_inputs=expected,
            items=("not-an-item",),  # type: ignore[arg-type]
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "malformed_item"


def test_reject_non_manifest_inputs_type() -> None:
    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=object(),  # type: ignore[arg-type]
            claimed_inputs=object(),  # type: ignore[arg-type]
            items=(),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "input_digest_mismatch"


def test_reject_pinned_inputs_with_wrong_contract_for_framework() -> None:
    from dataclasses import replace

    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    # Control plane accidentally pinned a playwright contract for a pytest suite.
    expected = replace(_inputs(), collection_contract_version="qep.playwright-collection.v1")
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=expected,
            claimed_inputs=expected,
            items=(
                _item(
                    item_index=0,
                    stable_case_id="case-1",
                    locator=_pytest_locator("case-1"),
                ),
            ),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "unsupported_collection_contract"


def test_reject_non_framework_locator_object() -> None:
    from qarunner.domain import (
        CollectionAdapterRejected,
        ManifestItem,
        accept_collection_adapter_result,
    )

    expected = _inputs()
    item = _item(
        item_index=0,
        stable_case_id="case-1",
        locator=_pytest_locator("case-1"),
    )
    # Bypass dataclass validation to simulate a corrupted adapter payload field.
    object.__setattr__(item, "framework_locator", "not-a-locator")
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=expected,
            claimed_inputs=expected,
            items=(item,),
            manifest_id="manifest-001",
            batch_id="batch-001",
        )
    assert caught.value.reason == "malformed_locator"
    assert isinstance(item, ManifestItem)


def test_reject_malformed_manifest_identity() -> None:
    from qarunner.domain import CollectionAdapterRejected, accept_collection_adapter_result

    expected = _inputs()
    with pytest.raises(CollectionAdapterRejected) as caught:
        accept_collection_adapter_result(
            framework="pytest",
            expected_inputs=expected,
            claimed_inputs=expected,
            items=(
                _item(
                    item_index=0,
                    stable_case_id="case-1",
                    locator=_pytest_locator("case-1"),
                ),
            ),
            manifest_id="",
            batch_id="batch-001",
        )
    assert caught.value.reason == "malformed_manifest"

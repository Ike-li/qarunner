"""T-M0-PORT-001 opaque ID contract."""

import pytest
from tests.fakes.greenfield.ids import FixedSequenceIdGenerator

from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.ids import OpaqueIdGenerator


def test_fake_id_generator_returns_only_the_injected_128_bit_sequence() -> None:
    first = "018f8f325a667c10a3f03a827a451111"
    second = "018f8f325a667c10a3f03a827a452222"
    generator = FixedSequenceIdGenerator((first, second))

    assert isinstance(generator, OpaqueIdGenerator)
    assert generator.new_id() == first
    assert generator.new_id() == second


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        (("",), "not_opaque_id"),
        ((None,), "not_opaque_id"),
        (
            (
                "018f8f32-5a66-7c10-a3f0-3a827a451111",
                "018f8f32-5a66-7c10-a3f0-3a827a451111",
            ),
            "duplicate",
        ),
    ],
)
def test_fake_id_generator_rejects_invalid_or_duplicate_seed(
    values: tuple[object, ...], reason: str
) -> None:
    with pytest.raises(PortContractError) as captured:
        FixedSequenceIdGenerator(values)  # type: ignore[arg-type]

    assert captured.value.resource == "id_generator"
    assert captured.value.field == "values"
    assert captured.value.reason == reason


def test_fake_id_generator_fails_closed_when_sequence_is_exhausted() -> None:
    generator = FixedSequenceIdGenerator(())

    with pytest.raises(PortContractError) as captured:
        generator.new_id()

    assert captured.value.resource == "id_generator"
    assert captured.value.field == "values"
    assert captured.value.reason == "exhausted"

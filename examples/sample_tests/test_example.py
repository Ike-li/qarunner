"""Sample tests for e2e smoke testing."""


def test_passing():
    """This test always passes."""
    assert 1 + 1 == 2


def test_also_passing():
    """Another passing test."""
    assert "hello".upper() == "HELLO"


def test_failing():
    """This test always fails — used to verify failure detection."""
    raise AssertionError("intentional failure")

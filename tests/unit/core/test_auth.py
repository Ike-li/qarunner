"""Unit tests for core authentication utility functions."""

from __future__ import annotations

from qarunner.core.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hashing_and_verification() -> None:
    """Test that password hashing and verification work as expected."""
    plain = "secure_password_123"
    hashed = hash_password(plain)
    assert hashed != plain
    assert verify_password(plain, hashed) is True
    assert verify_password("wrong_password", hashed) is False


def test_verify_password_invalid_hash() -> None:
    """Test verify_password returns False when matching against malformed/invalid hashes."""
    assert verify_password("some_password", "not_a_valid_bcrypt_hash") is False


def test_jwt_generation_and_decoding() -> None:
    """Test that creating and decoding an access token works correctly."""
    username = "test_user"
    role = "admin"
    token = create_access_token(username, role)
    assert isinstance(token, str)

    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == username
    assert payload["role"] == role
    assert "exp" in payload


def test_decode_invalid_jwt() -> None:
    """Test that decoding an invalid token returns None."""
    assert decode_access_token("not-a-valid-token") is None
    assert decode_access_token("") is None

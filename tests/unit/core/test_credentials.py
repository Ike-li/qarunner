from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

from qarunner.core.credentials import CredentialCipher


def test_encrypt_decrypt_roundtrip() -> None:
    cipher = CredentialCipher("super-secret-key-123")
    token = cipher.encrypt("ghp_mytoken")
    # The stored form must not be the plaintext.
    assert "ghp_mytoken" not in token
    assert cipher.decrypt(token) == "ghp_mytoken"


def test_wrong_key_cannot_decrypt() -> None:
    token = CredentialCipher("key-A").encrypt("secret")
    with pytest.raises(InvalidToken):
        CredentialCipher("key-B").decrypt(token)


def test_same_key_different_instances_interoperate() -> None:
    token = CredentialCipher("shared-key").encrypt("s3cr3t")
    assert CredentialCipher("shared-key").decrypt(token) == "s3cr3t"


def test_encryption_is_nondeterministic() -> None:
    # Fernet embeds a random IV, so the same plaintext encrypts differently each
    # time — a stored ciphertext can't be matched back by equality.
    cipher = CredentialCipher("k")
    assert cipher.encrypt("same") != cipher.encrypt("same")

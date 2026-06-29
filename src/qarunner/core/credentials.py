"""Symmetric encryption for stored git credentials (P0-1).

Credentials (HTTPS tokens) are encrypted at rest with Fernet (AES-128-CBC +
HMAC). The Fernet key is derived from the app ``SECRET_KEY`` via HKDF, so there's
no second secret to manage, while the HKDF ``info`` string domain-separates this
key from JWT signing — leaking one derived use doesn't expose the other.

Plaintext secrets are write-only: they're encrypted on the way in and only ever
decrypted inside the git-auth injection path, never returned by the API.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Bumping this string rotates the derived key (and invalidates stored secrets),
# so treat it as a version tag for the credential-encryption scheme.
_HKDF_INFO = b"qarunner-credential-encryption-v1"


def _derive_key(secret_key: str) -> bytes:
    """Derive a urlsafe-base64 Fernet key from *secret_key* via HKDF-SHA256."""
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO)
    return base64.urlsafe_b64encode(hkdf.derive(secret_key.encode("utf-8")))


class CredentialCipher:
    """Encrypt/decrypt credential secrets keyed off the app SECRET_KEY."""

    def __init__(self, secret_key: str) -> None:
        self._fernet = Fernet(_derive_key(secret_key))

    def encrypt(self, plaintext: str) -> str:
        """Return the encrypted, storable form of *plaintext*."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Recover the plaintext from a stored ciphertext (raises on tamper)."""
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")

"""AES-256-GCM under a ring of deployment secrets (ADR-KEY-01): seal under the first, open under any.

The egress proxy's durable state and the sealed credentials in sandbox routes both derive their key from the
deployment secret. While ``SECRET_KEY`` is rotated a reader also holds the other key of the ring, so a record sealed
before the switch still opens; nothing is ever sealed under it. GCM authenticates, so trying a key that did not seal
a record fails cleanly instead of yielding garbage.

Needs the ``sealing`` extra (``cryptography``).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from collections.abc import Sequence


_KEY_BYTES = 32


def derived_key(secret: bytes, label: bytes) -> bytes:
    """The AES key a ring member seals under: HMAC-SHA256 of the secret under the sealer's label."""
    return hmac.new(secret, label, hashlib.sha256).digest()


class AeadRing:
    def __init__(self, secrets: Sequence[bytes], label: bytes) -> None:
        if not secrets or not all(secrets):
            raise ValueError("a sealing secret is empty")
        self._members = tuple(AESGCM(derived_key(secret, label)) for secret in secrets)

    @classmethod
    def of_keys(cls, keys: Sequence[bytes]) -> AeadRing:
        """A ring over keys already derived (``derived_key``), the writer's first: what a service holding only the
        sealing keys, never the deployment secret, is given (ADR-KEY-02)."""
        if not keys or any(len(key) != _KEY_BYTES for key in keys):
            raise ValueError("a sealing key is not 32 bytes")
        ring = cls.__new__(cls)
        ring._members = tuple(AESGCM(key) for key in keys)
        return ring

    def encrypt(self, nonce: bytes, data: bytes, aad: bytes) -> bytes:
        return self._members[0].encrypt(nonce, data, aad)

    def decrypt(self, nonce: bytes, data: bytes, aad: bytes) -> tuple[bytes, bool]:
        """The plaintext, and whether the current key opened it; ``InvalidTag`` when no key does."""
        for index, member in enumerate(self._members):
            try:
                return member.decrypt(nonce, data, aad), index == 0
            except InvalidTag:
                continue
        raise InvalidTag


__all__ = ["AeadRing", "derived_key"]

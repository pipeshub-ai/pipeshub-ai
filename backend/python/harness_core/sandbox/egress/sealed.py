"""Credentials a trusted caller hands the egress proxy inside a route, sealed (OWD-1, WP-06).

A caller that holds a secret a sandbox needs (an operator's registry or database password, an MCP server's own
key) seals it into the route's ``credential_ref``. Only holders of the deployment secret open it: the proxy, which
injects it, and the trusted services that sealed it. The sandbox never sees the spec. The placeholder is the
associated data, so a sealed credential opens only on its own route; the broker, its logs and the proxy's durable
state carry ciphertext.
"""

from __future__ import annotations

import base64
import os
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from cryptography.exceptions import InvalidTag

from harness_core.sandbox.egress.aead_ring import AeadRing
from harness_core.sandbox.egress.errors import TokenUnavailable
from harness_core.sandbox.egress.types import IssuedToken

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from harness_core.clock import Clock
    from harness_core.sandbox.egress.ports import TokenSource
    from harness_core.sandbox.egress.types import EgressBinding
    from harness_core.sandbox.types import TokenRoute

SEALED_PREFIX: Final = "sealed:v1."
# A stand-in a sandbox holds instead of a secret: recognisable, matches no vendor credential shape, long enough for
# the proxy's checks, and unique per sandbox.
PLACEHOLDER_PREFIX: Final = "pxh-ph-"
_KEY_LABEL: Final = b"pipeshub egress sealed credential v1"
# Node derives the same key for the egress proxy (ADR-KEY-02); keep the two in step.
CREDENTIAL_KEY_LABEL: Final = _KEY_LABEL
_NONCE_BYTES: Final = 12
# A sealed credential does not expire; the proxy re-opens it this often, as it refreshes any token.
SEALED_TTL_S: Final = 3600


def new_placeholder() -> str:
    return PLACEHOLDER_PREFIX + secrets.token_hex(16)


def is_sealed(credential_ref: str) -> bool:
    return credential_ref.startswith(SEALED_PREFIX)


class CredentialSealer:
    """AES-256-GCM under a key derived from the deployment secret; the route's placeholder is the associated data.

    ``accepted`` is the key ring's other secret (ADR-KEY-01): a credential sealed under it still opens."""

    def __init__(
        self, secret: bytes, *, accepted: Sequence[bytes] = (), nonce: Callable[[int], bytes] = os.urandom
    ) -> None:
        if not secret:
            raise ValueError("sealing secret is empty")
        self._aead = AeadRing((secret, *accepted), _KEY_LABEL)
        self._nonce = nonce

    @classmethod
    def of_keys(cls, keys: Sequence[bytes], *, nonce: Callable[[int], bytes] = os.urandom) -> CredentialSealer:
        """A sealer over keys already derived from the ring (``LABEL``), the writer's first (ADR-KEY-02)."""
        sealer = cls.__new__(cls)
        sealer._aead = AeadRing.of_keys(keys)
        sealer._nonce = nonce
        return sealer

    def seal(self, placeholder: str, credential: str) -> str:
        nonce = self._nonce(_NONCE_BYTES)
        sealed = nonce + self._aead.encrypt(nonce, credential.encode("utf-8"), placeholder.encode("utf-8"))
        return SEALED_PREFIX + base64.urlsafe_b64encode(sealed).decode("ascii")

    def open(self, route: TokenRoute) -> str:
        return self.open_detailed(route.placeholder, route.credential_ref)[0]

    def open_detailed(self, placeholder: str, credential_ref: str) -> tuple[str, bool]:
        """The credential, and whether the current key sealed it (False: the ring's other key did)."""
        if not is_sealed(credential_ref):
            raise TokenUnavailable("not a sealed credential")
        try:
            raw = base64.urlsafe_b64decode(credential_ref[len(SEALED_PREFIX) :].encode("ascii"))
            if len(raw) <= _NONCE_BYTES:
                raise ValueError("short")
            plain, current = self._aead.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], placeholder.encode("utf-8"))
            return plain.decode("utf-8"), current
        except (ValueError, UnicodeError, InvalidTag):
            raise TokenUnavailable("the sealed credential does not open here") from None


class SealedTokenSource:
    """``TokenSource`` for sealed routes: the credential is the route's own, opened when the proxy needs it."""

    def __init__(self, sealer: CredentialSealer, clock: Clock, *, ttl_s: int = SEALED_TTL_S) -> None:
        self._sealer = sealer
        self._clock = clock
        self._ttl = timedelta(seconds=ttl_s)

    async def issue(self, route: TokenRoute, binding: EgressBinding) -> IssuedToken:
        del binding
        return IssuedToken(value=self._sealer.open(route), expires_at=self._clock.now() + self._ttl)


class RoutedTokenSource:
    """Sealed routes open with ``sealed``; every other route is a run-bound token from ``tokens``."""

    def __init__(self, tokens: TokenSource, sealed: TokenSource | None) -> None:
        self._tokens = tokens
        self._sealed = sealed

    async def issue(self, route: TokenRoute, binding: EgressBinding) -> IssuedToken:
        if not is_sealed(route.credential_ref):
            return await self._tokens.issue(route, binding)
        if self._sealed is None:
            raise TokenUnavailable("this proxy cannot open sealed credentials")
        return await self._sealed.issue(route, binding)


__all__ = [
    "PLACEHOLDER_PREFIX",
    "SEALED_PREFIX",
    "SEALED_TTL_S",
    "CREDENTIAL_KEY_LABEL",
    "CredentialSealer",
    "RoutedTokenSource",
    "SealedTokenSource",
    "is_sealed",
    "new_placeholder",
]

"""Compact JWS (RFC 7515) over Ed25519, verified against an issuer's published keys.

One verification core for every token Node signs with a ``SigningKeyring`` and Python verifies: the header is
exactly ``{alg: EdDSA, typ: JWT, kid}``, one issuer and one audience, ``iat``/``exp`` in epoch seconds with a
maximum lifetime. A ``kid`` this process has not seen makes it re-read the keys once (rate limited), so a rotation
on another replica is picked up without a restart; a retired key verifies within its retention.

Each kind of token keeps its own refusal: the caller maps a ``JwsFailure`` to the exception it raises, and parses
its own claims (``claims``), so the core knows no reason code and no claim but ``iss``, ``aud``, ``iat``, ``exp``.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.clock import Clock
    from harness_core.gateway.ports import GatewayKeySource, PublishedKey, PublishedKeys

JWS_ALGORITHM: Final = "EdDSA"
MIN_REFRESH_INTERVAL_S: Final = 1.0

# Where verification stopped, in the order it checks. ``claims``: ``iat`` or ``exp`` is not a non-negative integer.
JwsFailure = Literal[
    "missing",
    "malformed",
    "algorithm",
    "header",
    "unknown_key",
    "key_type",
    "signature",
    "issuer",
    "audience",
    "claims",
    "lifetime",
    "expired",
]

_B64URL = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


@dataclass(frozen=True, slots=True)
class JwsProfile:
    """What one kind of token must say, and how long it and its signing key may live."""

    issuer: str
    audience: str
    max_lifetime_s: int
    max_token_length: int
    # A retired key keeps verifying for the longest token it signed plus a replica's stale keyring.
    retired_key_retention_ms: int


@dataclass(frozen=True, slots=True)
class VerifiedJws[T]:
    claims: T
    kid: str
    expires_at_s: int


class JwsVerifier:
    """``refuse`` builds the caller's exception for a failure; the verifier raises it."""

    def __init__(
        self,
        keys: GatewayKeySource,
        clock: Clock,
        profile: JwsProfile,
        refuse: Callable[[JwsFailure], Exception],
    ) -> None:
        self._keys = keys
        self._clock = clock
        self._profile = profile
        self._refuse = refuse
        self._parsed: dict[str, Ed25519PublicKey] = {}
        self._last_refresh: float | None = None

    async def verify[T](self, token: str | None, claims: Callable[[dict[str, object]], T]) -> VerifiedJws[T]:
        """Signature, issuer, audience, the caller's claims (``claims`` raises its own refusal), lifetime, expiry."""
        if not token:
            raise self._refuse("missing")
        if len(token) > self._profile.max_token_length:
            raise self._refuse("malformed")
        parts = token.split(".")
        if len(parts) != 3:
            raise self._refuse("malformed")
        header = self._json_object(self._segment(parts[0]))
        payload_raw, signature = self._segment(parts[1]), self._segment(parts[2])
        if header.get("alg") != JWS_ALGORITHM:
            raise self._refuse("algorithm")
        kid = header.get("kid")
        if set(header) != {"alg", "typ", "kid"} or header.get("typ") != "JWT" or not isinstance(kid, str) or not kid:
            raise self._refuse("header")
        key = await self._key(kid)
        try:
            key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
        except InvalidSignature as exc:
            raise self._refuse("signature") from exc
        payload = self._json_object(payload_raw)
        if payload.get("iss") != self._profile.issuer:
            raise self._refuse("issuer")
        if payload.get("aud") != self._profile.audience:
            raise self._refuse("audience")
        parsed = claims(payload)
        issued, expires = self._seconds(payload, "iat"), self._seconds(payload, "exp")
        if expires - issued > self._profile.max_lifetime_s:
            raise self._refuse("lifetime")
        if expires * 1000 <= self._now_ms():
            raise self._refuse("expired")
        return VerifiedJws(parsed, kid, expires)

    def _segment(self, part: str) -> bytes:
        if not part or any(c not in _B64URL for c in part):
            raise self._refuse("malformed")
        try:
            return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        except (binascii.Error, ValueError) as exc:
            raise self._refuse("malformed") from exc

    def _json_object(self, raw: bytes) -> dict[str, object]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise self._refuse("malformed") from exc
        if not isinstance(value, dict):
            raise self._refuse("malformed")
        return cast("dict[str, object]", value)

    def _seconds(self, payload: dict[str, object], claim: str) -> int:
        value = payload.get(claim)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise self._refuse("claims")
        return value

    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * 1000)

    async def _key(self, kid: str) -> Ed25519PublicKey:
        found = self._find(await self._keys.keys(), kid)
        if found is None and self._may_refresh():
            found = self._find(await self._keys.keys(refresh=True), kid)
        if found is None:
            raise self._refuse("unknown_key")
        parsed = self._parsed.get(found.kid)
        if parsed is None:
            loaded = load_pem_public_key(found.public_key_pem.encode("ascii"))
            if not isinstance(loaded, Ed25519PublicKey):
                raise self._refuse("key_type")
            parsed = self._parsed[found.kid] = loaded
        return parsed

    def _find(self, published: PublishedKeys | None, kid: str) -> PublishedKey | None:
        if published is None:
            return None
        now = self._now_ms()
        retention = self._profile.retired_key_retention_ms
        return next(
            (
                key
                for key in published.keys
                if key.kid == kid and (key.retired_at_ms is None or now - key.retired_at_ms < retention)
            ),
            None,
        )

    def _may_refresh(self) -> bool:
        now = self._clock.monotonic()
        if self._last_refresh is not None and now - self._last_refresh < MIN_REFRESH_INTERVAL_S:
            return False
        self._last_refresh = now
        return True


__all__ = ["JWS_ALGORITHM", "MIN_REFRESH_INTERVAL_S", "JwsFailure", "JwsProfile", "JwsVerifier", "VerifiedJws"]

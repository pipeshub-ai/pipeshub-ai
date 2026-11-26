"""A test issuer of gateway tokens: the same compact JWS Node signs, from an in-process key.

Production tokens are minted only by Node (``gateway-token-authority.ts``); this
is for tests and for hosts (Loom) that verify with their own keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from harness_core.gateway.memory import StaticGatewayKeySource
from harness_core.gateway.ports import PublishedKey, PublishedKeys
from harness_core.gateway.token import (
    GATEWAY_TOKEN_ALGORITHM,
    GATEWAY_TOKEN_AUDIENCE,
    GATEWAY_TOKEN_ISSUER,
    GATEWAY_TOKEN_TTL_S,
    binding_claims,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.clock import Clock
    from harness_core.gateway.types import GatewayBinding


def _segment(value: Mapping[str, object]) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encode_token(
    header: Mapping[str, object], payload: Mapping[str, object], key: Ed25519PrivateKey
) -> str:
    """Compact JWS as Node's codec writes it (``JSON.stringify`` key order, no padding)."""
    signing_input = f"{_segment(header)}.{_segment(payload)}"
    signature = key.sign(signing_input.encode("ascii"))
    return f"{signing_input}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def private_pem(key: Ed25519PrivateKey) -> str:
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")


def kid_for(key: Ed25519PrivateKey) -> str:
    """Node's derivation: ``hgk_`` + the first 22 base64url chars of sha256(SPKI DER)."""
    der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    digest = base64.urlsafe_b64encode(hashlib.sha256(der).digest()).rstrip(b"=").decode("ascii")
    return f"hgk_{digest[:22]}"


@dataclass
class _Key:
    private: Ed25519PrivateKey
    kid: str
    retired_at_ms: int | None = None


@dataclass
class TokenMint:
    """Signs gateway tokens and publishes its public keys through ``source``."""

    clock: Clock
    issuer: str = GATEWAY_TOKEN_ISSUER
    audience: str = GATEWAY_TOKEN_AUDIENCE
    source: StaticGatewayKeySource = field(default_factory=StaticGatewayKeySource)
    _keys: list[_Key] = field(default_factory=list[_Key])

    def __post_init__(self) -> None:
        self.rotate()

    @property
    def kid(self) -> str:
        return self._keys[-1].kid

    def rotate(self) -> str:
        """A new key signs from now on; the previous one is retired (and still published)."""
        if self._keys:
            self._keys[-1].retired_at_ms = self._now_ms()
        private = Ed25519PrivateKey.generate()
        self._keys.append(_Key(private, kid_for(private)))
        self._publish()
        return self.kid

    def forget_retired(self) -> None:
        self._keys = self._keys[-1:]
        self._publish()

    def mint(
        self,
        binding: GatewayBinding,
        *,
        ttl_s: int = GATEWAY_TOKEN_TTL_S,
        issued_at: int | None = None,
        header: Mapping[str, object] | None = None,
        claims: Mapping[str, object] | None = None,
        kid: str | None = None,
    ) -> str:
        """``header``/``claims`` override or add fields (``None`` values remove one)."""
        key = next(k for k in self._keys if k.kid == (kid or self.kid))
        iat = issued_at if issued_at is not None else self._now_ms() // 1000
        payload: dict[str, object] = {
            "iss": self.issuer,
            "aud": self.audience,
            **binding_claims(binding),
            "iat": iat,
            "exp": iat + ttl_s,
        }
        head: dict[str, object] = {"alg": GATEWAY_TOKEN_ALGORITHM, "typ": "JWT", "kid": key.kid}
        for target, overrides in ((head, header), (payload, claims)):
            for name, value in (overrides or {}).items():
                if value is None:
                    target.pop(name, None)
                else:
                    target[name] = value
        return encode_token(head, payload, key.private)

    def _publish(self) -> None:
        self.source.publish(
            PublishedKeys(
                self.issuer,
                self.audience,
                tuple(PublishedKey(k.kid, public_pem(k.private), k.retired_at_ms) for k in self._keys),
            )
        )

    def _now_ms(self) -> int:
        return int(self.clock.now().timestamp() * 1000)


__all__ = ["TokenMint", "encode_token", "kid_for", "private_pem", "public_pem"]

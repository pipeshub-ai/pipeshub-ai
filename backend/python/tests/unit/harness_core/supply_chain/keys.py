"""Generated Ed25519 keys for the supply-chain tests: never a real key (the release key is the owner's, OB-1)."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from harness_core.supply_chain.signatures import sign_detached
from harness_core.supply_chain.trust import ArtefactKind, Attestation, statement


@dataclass(frozen=True)
class TestKey:
    key_id: str
    private: bytes
    public: bytes

    @property
    def public_b64(self) -> str:
        return base64.b64encode(self.public).decode()

    def attest(
        self,
        kind: ArtefactKind,
        subject: str,
        digest: str,
        claims: dict[str, object] | None = None,
    ) -> Attestation:
        signed = statement(kind, subject, digest, claims)  # type: ignore[arg-type]
        return Attestation(
            kind,
            subject,
            digest,
            sign_detached(signed, self.private, self.key_id),
            dict(claims or {}),
        )  # type: ignore[arg-type]


def new_key(key_id: str) -> TestKey:
    private = Ed25519PrivateKey.generate()
    return TestKey(
        key_id,
        private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()),
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
    )

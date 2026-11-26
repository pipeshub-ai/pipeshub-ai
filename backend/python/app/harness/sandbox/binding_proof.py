"""The broker's proof of which run attempt a sandbox serves (R38IDENT-FU-7, ADR-CRED-01 D6).

The broker attaches it to every egress binding it registers. The egress proxy cannot make one (it holds no key that
does) and forwards it when it asks Node for that run's gateway token; Node issues only against a proof for exactly
that org, run and attempt. So the proxy's credential names no run by itself: a foothold in the proxy reaches only the
attempts whose sandboxes the broker put behind it, while they hold their lease.

Node's twin is ``backend/nodejs/apps/src/modules/agent-harness/domain/sandbox-binding.ts``;
``contracts/secret-key/sandbox-binding.json`` holds both to the same bytes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
from dataclasses import replace
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.org_context import org_of
from app.utils.encryption.component_key import component_identity, component_subkey
from app.utils.encryption.secret_key_ring import deployment_ring
from harness_core.engines.attempt_labels import bound_attempt

if TYPE_CHECKING:
    from harness_core.sandbox.egress.types import EgressBinding

BROKER = "sandbox-broker"
_MESSAGE_LABEL = b"pipeshub sandbox binding v1"
_RING_SALT = b"pipeshub/sandbox-binding"
_RING_INFO = b"ring/v1"


def binding_message(org_id: str, run_id: str, attempt_id: str) -> bytes:
    """Each field length-prefixed, so no choice of ids makes two bindings share bytes."""
    parts = [_MESSAGE_LABEL]
    for value in (org_id, run_id, attempt_id):
        raw = value.encode("utf-8")
        parts.append(struct.pack(">I", len(raw)) + raw)
    return b"".join(parts)


def ring_binding_key(secret: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_RING_SALT, info=_RING_INFO).derive(secret.encode("utf-8"))


def binding_proof(key: bytes, org_id: str, run_id: str, attempt_id: str) -> str:
    mac = hmac.new(key, binding_message(org_id, run_id, attempt_id), hashlib.sha256).digest()
    return "v1." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def broker_binding_key() -> bytes | None:
    """The key this broker signs under: its component key's subkey, or (a broker run holding ``SECRET_KEY``) the
    ring's writer key; ``None`` where it holds neither, and then no binding carries a proof."""
    identity = component_identity()
    if identity is not None:
        component, key = identity
        return component_subkey(BROKER, key, "binding") if component == BROKER else None
    ring = deployment_ring()
    return ring_binding_key(ring.current) if ring is not None else None


class BindingAttester:
    """Stamps a binding with the proof for the run attempt its labels name, as the proxy will read them."""

    def __init__(self, key: bytes | None) -> None:
        self._key = key

    @classmethod
    def from_env(cls) -> BindingAttester:
        return cls(broker_binding_key())

    def attest(self, binding: EgressBinding) -> EgressBinding:
        org_id, attempt = org_of(binding.labels), bound_attempt(binding.labels)
        if self._key is None or not org_id or attempt is None:
            return replace(binding, attestation=None)
        return replace(binding, attestation=binding_proof(self._key, org_id, *attempt))


__all__ = [
    "BindingAttester",
    "binding_message",
    "binding_proof",
    "broker_binding_key",
    "ring_binding_key",
]

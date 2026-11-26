"""The resolved-turn vector as test data, and a mint for headers the tests sign themselves."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config.constants.service import config_node_constants
from harness_core.gateway.ports import PublishedKey, PublishedKeys
from harness_core.testing.clock import ManualClock
from harness_core.testing.gateway import encode_token, kid_for, public_pem
from tests.support.resolved_turn_vector import VECTOR_PATH

VECTOR: dict[str, Any] = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
NOW: int = VECTOR["nowSeconds"]
KEYS_NODE: str = config_node_constants.AGENT_HARNESS_GATEWAY_PUBLIC_KEYS.value


def private_key(name: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(VECTOR["keys"][name]["jwk"]["d"] + "="))


ACTIVE = private_key("active")


def clock_at(seconds: int = NOW) -> ManualClock:
    return ManualClock(datetime.fromtimestamp(seconds, tz=UTC))


def published() -> PublishedKeys:
    active, retired = VECTOR["keys"]["active"], VECTOR["keys"]["retired"]
    return PublishedKeys(
        "pipeshub-node/agent-harness",
        "pipeshub-agent-harness/gateway",
        (
            PublishedKey(active["kid"], active["publicKeyPem"]),
            PublishedKey(retired["kid"], retired["publicKeyPem"], retired["retiredAtMs"]),
        ),
    )


def keys_document(*keys: Ed25519PrivateKey) -> dict[str, Any]:
    """Node's ``PublishedSigningKeys`` document, as the configuration store holds it."""
    return {
        "issuer": "pipeshub-node/agent-harness",
        "audience": "pipeshub-agent-harness/gateway",
        "keys": [{"kid": kid_for(key), "alg": "EdDSA", "publicKey": public_pem(key), "retiredAt": None} for key in keys],
    }


def claims(now: int = NOW, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    """The vector's valid payload issued at ``now``; an override of ``None`` removes an optional claim."""
    payload = {**VECTOR["valid"]["payload"], "iat": now, "exp": now + VECTOR["ttlSeconds"], **overrides}
    return {name: value for name, value in payload.items() if not (value is None and name in ("access", "bindings"))}


def sign(payload: dict[str, Any], key: Ed25519PrivateKey = ACTIVE) -> str:
    return encode_token({"alg": "EdDSA", "typ": "JWT", "kid": kid_for(key)}, payload, key)

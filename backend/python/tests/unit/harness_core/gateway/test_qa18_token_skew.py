"""QA18-4 (VS-6): the token issuer (Node) and the model gateway one release apart, in both orders.

Node signs the gateway token; the gateway (the worker's image) verifies it. A claim one side added that the other does
not know is a binding the gateway cannot check, so it refuses the token as malformed rather than verify what it can
read and ignore the rest. Tokens are signed with the cross-language vector's key, exactly as Node signs them.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from harness_core.gateway import (
    GatewayTokenVerifier,
    PublishedKey,
    PublishedKeys,
    StaticGatewayKeySource,
    TokenRefused,
)
from harness_core.testing.clock import ManualClock

VECTOR = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "gateway-token.json").read_text()
)
CURRENT: dict[str, Any] = VECTOR["tokens"][0]["payload"]
# Claims a release may add or retire; the envelope claims (issuer, audience, times, binding kind) are checked on their own.
BINDING_CLAIMS = sorted(set(CURRENT) - {"iss", "aud", "iat", "exp", "bnd"})


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(payload: dict[str, Any]) -> str:
    seed = base64.urlsafe_b64decode(VECTOR["jwk"]["d"] + "=" * (-len(VECTOR["jwk"]["d"]) % 4))
    signing_input = f"{_b64(json.dumps(VECTOR['header']).encode())}.{_b64(json.dumps(payload).encode())}"
    signature = Ed25519PrivateKey.from_private_bytes(seed).sign(signing_input.encode("ascii"))
    return f"{signing_input}.{_b64(signature)}"


def _gateway() -> GatewayTokenVerifier:
    keys = StaticGatewayKeySource(
        PublishedKeys(CURRENT["iss"], CURRENT["aud"], (PublishedKey(VECTOR["kid"], VECTOR["publicKeyPem"]),))
    )
    return GatewayTokenVerifier(keys, ManualClock(datetime.fromtimestamp(VECTOR["nowSeconds"], tz=UTC)))


async def test_qa18_4_the_current_issuers_token_verifies() -> None:
    claims = await _gateway().verify(_sign(CURRENT))
    assert claims.binding.run_id == CURRENT["runId"]


@pytest.mark.parametrize("added", ["modelPolicyDigest", "budgetUsd"])
async def test_qa18_4_a_newer_issuers_claim_is_refused_by_an_older_gateway(added: str) -> None:
    with pytest.raises(TokenRefused) as refused:
        _ = await _gateway().verify(_sign({**CURRENT, added: "sha256:" + "0" * 64}))
    assert refused.value.reason_code == "token_malformed"


@pytest.mark.parametrize("retired", BINDING_CLAIMS)
async def test_qa18_4_an_older_issuers_token_without_a_claim_is_refused_by_a_newer_gateway(retired: str) -> None:
    payload = {k: v for k, v in CURRENT.items() if k != retired}
    with pytest.raises(TokenRefused) as refused:
        _ = await _gateway().verify(_sign(payload))
    assert refused.value.reason_code == "token_malformed"

"""GT-1..GT-9: gateway token verification (Ed25519 compact JWS)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness_core.gateway import (
    GatewayTokenVerifier,
    PublishedKey,
    PublishedKeys,
    RunAttemptBinding,
    StaticGatewayKeySource,
    TokenRefused,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.tool_endpoint import BINDING
from harness_core.testing.gateway import TokenMint

VECTOR = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "gateway-token.json").read_text()
)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def mint(clock: ManualClock) -> TokenMint:
    return TokenMint(clock)


def verifier(mint: TokenMint, clock: ManualClock) -> GatewayTokenVerifier:
    return GatewayTokenVerifier(mint.source, clock)


async def refused(verify: GatewayTokenVerifier, token: str | None) -> str:
    with pytest.raises(TokenRefused) as refusal:
        await verify.verify(token)
    return refusal.value.reason_code


async def test_gt1_a_valid_token_names_its_binding(mint: TokenMint, clock: ManualClock) -> None:
    claims = await verifier(mint, clock).verify(mint.mint(BINDING))
    assert claims.binding == BINDING
    assert claims.kid == mint.kid
    assert claims.expires_at == int(clock.now().timestamp()) + 600


async def test_gt2_an_expired_token_is_refused(mint: TokenMint, clock: ManualClock) -> None:
    token = mint.mint(BINDING)
    clock.advance(599)
    await verifier(mint, clock).verify(token)
    clock.advance(1)
    assert await refused(verifier(mint, clock), token) == "token_expired"


async def test_gt3_an_unknown_kid_refreshes_once_then_is_refused(mint: TokenMint, clock: ManualClock) -> None:
    verify = verifier(mint, clock)
    token = mint.mint(BINDING, header={"kid": "hgk_unknown"})
    assert await refused(verify, token) == "token_unknown_key"
    assert mint.source.refreshes == 1
    assert await refused(verify, token) == "token_unknown_key"
    assert mint.source.refreshes == 1, "a second unknown kid within a second must not re-read the keys"


async def test_gt3_a_rotation_on_another_replica_is_picked_up(clock: ManualClock) -> None:
    mint = TokenMint(clock)
    stale = StaticGatewayKeySource(mint.source._keys)

    class Replica:
        async def keys(self, *, refresh: bool = False) -> PublishedKeys | None:
            return await (mint.source if refresh else stale).keys()

    verify = GatewayTokenVerifier(Replica(), clock)
    mint.rotate()
    assert (await verify.verify(mint.mint(BINDING))).kid == mint.kid


@pytest.mark.parametrize("alg", ["none", "HS256", "RS256"])
async def test_gt4_another_algorithm_is_refused(mint: TokenMint, clock: ManualClock, alg: str) -> None:
    assert await refused(verifier(mint, clock), mint.mint(BINDING, header={"alg": alg})) == "token_algorithm"


def _tamper(token: str, part: int, change: dict[str, object]) -> str:
    segments = token.split(".")
    raw = json.loads(base64.urlsafe_b64decode(segments[part] + "=="))
    raw.update(change)
    segments[part] = base64.urlsafe_b64encode(json.dumps(raw).encode()).rstrip(b"=").decode()
    return ".".join(segments)


async def test_gt5_a_tampered_payload_or_signature_is_refused(mint: TokenMint, clock: ManualClock) -> None:
    verify = verifier(mint, clock)
    token = mint.mint(BINDING)
    assert await refused(verify, _tamper(token, 1, {"runId": "run-other"})) == "token_signature"
    head, body, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    assert await refused(verify, f"{head}.{body}.{flipped}") == "token_signature"


async def test_gt6_another_audience_or_issuer_is_refused(clock: ManualClock) -> None:
    attempt_tokens = TokenMint(clock, audience="pipeshub-agent-harness/attempt")
    assert await refused(verifier(attempt_tokens, clock), attempt_tokens.mint(BINDING)) == "token_audience"
    other_issuer = TokenMint(clock, issuer="someone-else")
    assert await refused(verifier(other_issuer, clock), other_issuer.mint(BINDING)) == "token_issuer"


@pytest.mark.parametrize(
    ("token", "claims", "header"),
    [
        ("", None, None),
        ("a.b", None, None),
        ("a.b.c.d", None, None),
        ("@@@.b.c", None, None),
        (None, {"extra": "claim"}, None),
        (None, {"runId": None}, None),
        (None, {"bnd": "proxy"}, None),
        (None, {"fence": "2"}, None),
        (None, {"orgId": "x" * 129}, None),
        (None, {"exp": 4_102_444_800}, None),
        (None, None, {"typ": "JOSE"}),
        (None, None, {"cty": "JWT"}),
    ],
    ids=[
        "empty",
        "two-segments",
        "four-segments",
        "not-base64url",
        "extra-claim",
        "missing-claim",
        "unknown-binding",
        "fence-not-int",
        "id-too-long",
        "lifetime-too-long",
        "wrong-typ",
        "extra-header",
    ],
)
async def test_gt7_malformed_tokens_are_refused(
    mint: TokenMint,
    clock: ManualClock,
    token: str | None,
    claims: dict[str, object] | None,
    header: dict[str, object] | None,
) -> None:
    presented = token if token is not None else mint.mint(BINDING, claims=claims, header=header)
    reason = await refused(verifier(mint, clock), presented)
    assert reason == ("token_missing" if presented == "" else "token_malformed")


async def test_gt7_an_oversized_token_is_malformed(mint: TokenMint, clock: ManualClock) -> None:
    assert await refused(verifier(mint, clock), "a" * 5000) == "token_malformed"


async def test_gt8_a_retired_key_verifies_within_its_retention_only(mint: TokenMint, clock: ManualClock) -> None:
    old_kid = mint.kid
    token = mint.mint(BINDING, ttl_s=600)
    mint.rotate()
    clock.advance(300)
    assert (await verifier(mint, clock).verify(token)).kid == old_kid
    late = mint.mint(BINDING, kid=old_kid)
    clock.advance(15 * 60 - 300)
    assert await refused(verifier(mint, clock), late) == "token_unknown_key"


def _vector_verifier() -> GatewayTokenVerifier:
    clock = ManualClock(datetime.fromtimestamp(VECTOR["nowSeconds"], tz=UTC))
    keys = StaticGatewayKeySource(
        PublishedKeys(
            "pipeshub-node/agent-harness",
            "pipeshub-agent-harness/gateway",
            (PublishedKey(VECTOR["kid"], VECTOR["publicKeyPem"]),),
        )
    )
    return GatewayTokenVerifier(keys, clock)


@pytest.mark.parametrize("entry", VECTOR["refused"], ids=lambda entry: entry["binding"])
async def test_sr_8_nodes_stage_1_request_token_is_refused_as_malformed(entry: dict[str, object]) -> None:
    assert await refused(_vector_verifier(), str(entry["token"])) == entry["reason"]


@pytest.mark.parametrize("index", [0], ids=["run"])
async def test_gt9_nodes_vector_verifies_in_python(index: int) -> None:
    clock = ManualClock(datetime.fromtimestamp(VECTOR["nowSeconds"], tz=UTC))
    keys = StaticGatewayKeySource(
        PublishedKeys(
            "pipeshub-node/agent-harness",
            "pipeshub-agent-harness/gateway",
            (PublishedKey(VECTOR["kid"], VECTOR["publicKeyPem"]),),
        )
    )
    entry = VECTOR["tokens"][index]
    claims = await GatewayTokenVerifier(keys, clock).verify(entry["token"])
    payload = entry["payload"]
    assert entry["binding"] == "run"
    assert claims.binding == RunAttemptBinding(
        payload["orgId"],
        payload["sessionId"],
        payload["runId"],
        payload["parentRunId"],
        payload["attemptId"],
        payload["fence"],
        payload["toolIdsDigest"],
        payload["routesDigest"],
    )

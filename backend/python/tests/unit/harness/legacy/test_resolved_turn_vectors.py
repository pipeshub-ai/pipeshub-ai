"""RTE-22b/LEG-10, RTE-22c, RTE-22d: the query service's verifier of ``X-PipesHub-Resolved`` on the shared vector."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.gateway.keys import ConfiguredGatewayKeys
from app.harness.legacy.errors import RESOLVED_TURN_REFUSAL_CODES, ResolvedTurnRefused
from app.harness.legacy.resolved_turn import (
    MAX_RESOLVED_TURN_LENGTH,
    MAX_RESOLVED_TURN_LIFETIME_S,
    RESOLVED_TURN_AUDIENCE,
    RESOLVED_TURN_ISSUER,
    RESOLVED_TURN_TTL_S,
    RESOLVED_TURN_VERSION,
    ResolvedBinding,
    ResolvedTurn,
    ResolvedTurnVerifier,
)
from harness_core.effects.flow import FlowRule
from harness_core.gateway import (
    GatewayTokenVerifier,
    StaticGatewayKeySource,
    TokenRefused,
)
from harness_core.gateway.token import RETIRED_KEY_RETENTION_MS
from tests.support.config_service import FakeConfigurationService
from tests.support.resolved_turn_vector import VECTOR_PATH, render
from tests.unit.harness.legacy.support import (
    ACTIVE,
    KEYS_NODE,
    NOW,
    VECTOR,
    claims,
    clock_at,
    keys_document,
    private_key,
    published,
    sign,
)

if TYPE_CHECKING:
    from harness_core.testing.clock import ManualClock

REQUEST: dict[str, Any] = VECTOR["binding"]["request"]


def _for(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "org_id": request["orgId"],
        "user_id": request["userId"],
        "agent_key": request["agentKey"],
        "conversation_id": request["conversationId"],
    }


def _verifier(clock: ManualClock | None = None, source: StaticGatewayKeySource | None = None) -> ResolvedTurnVerifier:
    return ResolvedTurnVerifier(source or StaticGatewayKeySource(published()), clock or clock_at())


async def _refused(verifier: ResolvedTurnVerifier, token: str, request: dict[str, Any] = REQUEST) -> str:
    with pytest.raises(ResolvedTurnRefused) as refusal:
        await verifier.verify(token, **_for(request))
    assert refusal.value.http_status == 401
    if len(token) > 40:
        assert token not in str(refusal.value) and token not in str(refusal.value.detail())
    return refusal.value.reason_code


def test_the_committed_vector_is_the_generator_s_output() -> None:
    assert VECTOR_PATH.read_text(encoding="utf-8") == render()


NODE_SIGNED: dict[str, Any] = json.loads(VECTOR_PATH.with_name("resolved-turn-node.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", NODE_SIGNED["cases"], ids=lambda case: case["id"])
async def test_a_header_node_projected_from_a_pin_and_signed_verifies_and_reads_back(case: dict[str, Any]) -> None:
    """``resolved-turn-node.json`` holds the bytes Node's signer makes from a pin (its test rebuilds them): the
    strict parser accepts every claim Node sends and reads what Node meant."""
    assert NODE_SIGNED["nowSeconds"] == NOW
    claims = case["claims"]
    turn = await _verifier().verify(case["token"], **_for(case["request"]))
    assert (turn.org, turn.sub, turn.agent, turn.cid) == (claims["org"], claims["sub"], claims["agent"], claims["cid"])
    assert (turn.pin.digest, turn.pin.head) == (claims["pin"]["digest"], claims["pin"]["head"])
    assert (turn.pin.routing.path, turn.pin.routing.reason) == ("legacy", claims["pin"]["routing"]["reason"])
    assert (turn.pin.scope.agent_key, turn.pin.scope.project_id) == (
        claims["pin"]["scope"]["agentKey"],
        claims["pin"]["scope"]["projectId"],
    )
    assert dict(turn.stages) == claims["stages"]
    assert turn.flow == tuple(FlowRule(rule["level"], rule["above"]) for rule in claims["flow"])
    assert turn.access_run is ("access" in claims)
    assert turn.bindings == tuple(
        ResolvedBinding(binding["installation"], binding["owner"], binding["ref"]) for binding in claims.get("bindings", [])
    )


def test_the_node_signed_cases_cover_an_agent_with_bindings_and_the_assistant() -> None:
    cases = {case["id"]: case["claims"] for case in NODE_SIGNED["cases"]}
    assert cases["agent-turn"]["access"] == {"run": True}
    assert {binding["owner"].partition(":")[0] for binding in cases["agent-turn"]["bindings"]} == {"user", "agent"}
    assert cases["agent-turn"]["flow"][0] == {"level": "ask", "above": "public"}
    assert cases["assistant-turn"]["agent"] is None and "bindings" not in cases["assistant-turn"]


def test_the_vector_states_the_verifier_s_constants() -> None:
    assert (VECTOR["issuer"], VECTOR["audience"]) == (RESOLVED_TURN_ISSUER, RESOLVED_TURN_AUDIENCE)
    assert (VECTOR["version"], VECTOR["ttlSeconds"]) == (RESOLVED_TURN_VERSION, RESOLVED_TURN_TTL_S)
    assert VECTOR["maxLifetimeSeconds"] == MAX_RESOLVED_TURN_LIFETIME_S
    assert VECTOR["maxTokenLength"] == MAX_RESOLVED_TURN_LENGTH
    assert VECTOR["retentionMs"] == RETIRED_KEY_RETENTION_MS


async def test_rte22b_leg10_the_valid_token_is_the_turn_node_resolved() -> None:
    payload = VECTOR["valid"]["payload"]
    turn = await _verifier().verify(VECTOR["valid"]["token"], **_for(REQUEST))
    assert isinstance(turn, ResolvedTurn)
    assert (turn.org, turn.sub, turn.agent, turn.cid) == ("org-1", "user-1", "agent-a", "conv-1")
    assert (turn.pin.digest, turn.pin.head) == (payload["pin"]["digest"], 41)
    assert (turn.pin.routing.path, turn.pin.routing.reason) == ("legacy", "chat_mode_not_supported")
    assert (turn.pin.scope.agent_key, turn.pin.scope.project_id, turn.pin.scope.agent_owner) == ("agent-a", None, None)
    assert dict(turn.stages) == payload["stages"]
    assert turn.flow == (FlowRule("ask", "public"), FlowRule("deny", "private"))
    assert turn.access_run is True
    assert turn.bindings == (
        ResolvedBinding("inst-jira", "user:user-1", "bnd_01J9ZX3K7M2Q4R6T8V0W1Y3Z5A"),
        ResolvedBinding("inst-mcp", "agent:agent-a", "bnd_01J9ZX3K7M2Q4R6T8V0W1Y3Z5B"),
    )
    assert turn.bound_installations("user:user-1") == {"inst-jira"}
    assert turn.bound_installations("agent:agent-a") == {"inst-mcp"}
    assert turn.bound_installations("user:user-2") == frozenset()


@pytest.mark.parametrize("case", VECTOR["refused"], ids=lambda case: case["id"])
async def test_rte22b_leg10_a_refused_token_is_refused_with_the_vector_s_reason(case: dict[str, Any]) -> None:
    assert await _refused(_verifier(), case["token"]) == case["reason"]


def test_rte22b_the_vector_covers_every_refusal_the_design_names() -> None:
    assert {case["id"] for case in VECTOR["refused"]} == {
        "unknown-kid", "expired", "tampered-payload", "tampered-signature", "alg-none", "gateway-token",
        "wrong-issuer", "lifetime-too-long", "unknown-version", "durable-routing", "missing-claim", "extra-claim",
    }
    reasons = {case["reason"] for group in (VECTOR["refused"], VECTOR["binding"]["refused"]) for case in group}
    assert reasons <= set(RESOLVED_TURN_REFUSAL_CODES)
    assert set(RESOLVED_TURN_REFUSAL_CODES) - reasons == {"resolved_required"}, "the route's own, not a token's"


async def test_rte22b_a_resolved_turn_never_verifies_as_a_gateway_token() -> None:
    gateway = GatewayTokenVerifier(StaticGatewayKeySource(published()), clock_at())
    with pytest.raises(TokenRefused) as refusal:
        await gateway.verify(VECTOR["valid"]["token"])
    assert refusal.value.reason_code == "token_audience"


@pytest.mark.parametrize("case", VECTOR["binding"]["refused"], ids=lambda case: case["id"])
async def test_rte22c_the_valid_token_serves_only_the_request_it_was_signed_for(case: dict[str, Any]) -> None:
    assert await _refused(_verifier(), VECTOR["valid"]["token"], case["request"]) == case["reason"]


async def test_rte22c_the_assistant_s_turn_is_bound_to_no_agent() -> None:
    token = sign(claims(agent=None))
    turn = await _verifier().verify(token, **_for({**REQUEST, "agentKey": None}))
    assert turn.agent is None
    assert await _refused(_verifier(), token) == "resolved_agent_mismatch"


async def test_rte22d_two_live_keys_verify_and_a_retired_one_only_inside_its_retention() -> None:
    clock = clock_at()
    verifier = _verifier(clock)
    assert (await verifier.verify(VECTOR["valid"]["token"], **_for(REQUEST))).cid == "conv-1"
    assert (await verifier.verify(VECTOR["retired"]["token"], **_for(REQUEST))).cid == "conv-1"

    retired_at_s = VECTOR["keys"]["retired"]["retiredAtMs"] // 1000
    last = retired_at_s + RETIRED_KEY_RETENTION_MS // 1000 - 1
    retired = private_key("retired")
    clock.advance(last - NOW)
    assert (await verifier.verify(sign(claims(last), retired), **_for(REQUEST))).cid == "conv-1"
    clock.advance(1)
    assert await _refused(verifier, sign(claims(last + 1), retired)) == "resolved_unknown_key"
    assert (await verifier.verify(sign(claims(last + 1)), **_for(REQUEST))).cid == "conv-1"


async def test_rte22d_a_kid_first_seen_re_reads_the_keys_exactly_once() -> None:
    """A rotation on another Node replica: this process's cached keys do not hold the new ``kid`` yet."""
    stale, current = StaticGatewayKeySource(None), StaticGatewayKeySource(published())

    class Replica(StaticGatewayKeySource):
        async def keys(self, *, refresh: bool = False):  # noqa: ANN202
            self.refreshes += refresh
            return await (current if refresh else stale).keys()

    source, clock = Replica(), clock_at()
    verifier = _verifier(clock, source)
    assert (await verifier.verify(VECTOR["valid"]["token"], **_for(REQUEST))).cid == "conv-1"
    assert source.refreshes == 1

    unknown = next(case for case in VECTOR["refused"] if case["id"] == "unknown-kid")["token"]
    assert await _refused(verifier, unknown) == "resolved_unknown_key"
    assert source.refreshes == 1, "a second unknown kid within a second must not re-read the keys"
    clock.advance(1)
    assert await _refused(verifier, unknown) == "resolved_unknown_key"
    assert source.refreshes == 2


@pytest.mark.parametrize("document", [None, "ciphertext", {"issuer": 1}, {"issuer": "i", "audience": "a", "keys": []}])
async def test_rte22d_unreadable_or_absent_keys_refuse_every_header(document: object) -> None:
    config = FakeConfigurationService({KEYS_NODE: document} if document is not None else None)
    verifier = ResolvedTurnVerifier(ConfiguredGatewayKeys(config), clock_at())
    assert await _refused(verifier, VECTOR["valid"]["token"]) == "resolved_unknown_key"


async def test_rte22d_the_published_keys_are_read_once_for_many_turns() -> None:
    config = FakeConfigurationService({KEYS_NODE: keys_document(ACTIVE)})
    verifier = ResolvedTurnVerifier(ConfiguredGatewayKeys(config), clock_at())
    for _ in range(5):
        _ = await verifier.verify(VECTOR["valid"]["token"], **_for(REQUEST))
    assert config.reads.count(KEYS_NODE) == 1


async def test_expiry_is_the_verifier_s_clock() -> None:
    clock = clock_at()
    verifier = _verifier(clock)
    token = sign(claims())
    clock.advance(RESOLVED_TURN_TTL_S - 1)
    assert (await verifier.verify(token, **_for(REQUEST))).cid == "conv-1"
    clock.advance(1)
    assert await _refused(verifier, token) == "resolved_expired"


async def test_a_lifetime_up_to_the_maximum_is_accepted_and_one_second_more_is_malformed() -> None:
    verifier = _verifier()
    assert await verifier.verify(sign(claims(exp=NOW + MAX_RESOLVED_TURN_LIFETIME_S)), **_for(REQUEST))
    assert await _refused(verifier, sign(claims(exp=NOW + MAX_RESOLVED_TURN_LIFETIME_S + 1))) == "resolved_malformed"


async def test_a_header_with_many_bindings_fits_and_one_over_the_limit_is_malformed() -> None:
    def bindings(count: int) -> list[dict[str, str]]:
        return [{"installation": f"inst-{n:04d}", "owner": "user:user-1", "ref": f"bnd-{n:04d}"} for n in range(count)]

    verifier = _verifier()
    fits = sign(claims(bindings=bindings(100)))
    assert 8 * 1024 < len(fits) <= MAX_RESOLVED_TURN_LENGTH
    assert len((await verifier.verify(fits, **_for(REQUEST))).bindings) == 100
    over = sign(claims(bindings=bindings(200)))
    assert len(over) > MAX_RESOLVED_TURN_LENGTH
    assert await _refused(verifier, over) == "resolved_malformed"


async def test_access_and_bindings_are_the_only_optional_claims() -> None:
    turn = await _verifier().verify(sign(claims(access=None, bindings=None)), **_for(REQUEST))
    assert turn.access_run is False
    assert turn.bindings == ()
    assert (await _verifier().verify(sign(claims(access={"run": False})), **_for(REQUEST))).access_run is False


_PIN = VECTOR["valid"]["payload"]["pin"]
MALFORMED: dict[str, dict[str, Any]] = {
    "org not a string": {"org": 7},
    "an empty user": {"sub": ""},
    "an empty conversation": {"cid": ""},
    "an empty agent": {"agent": ""},
    "stages not an object": {"stages": ["ENABLE_ACTIONS"]},
    "a stage that is not a boolean": {"stages": {"ENABLE_ACTIONS": "false"}},
    "a stage that is a number": {"stages": {"ENABLE_ACTIONS": 0}},
    "flow not a list": {"flow": {"level": "ask", "above": "public"}},
    "a flow level nobody knows": {"flow": [{"level": "off", "above": "public"}]},
    "a flow threshold nobody knows": {"flow": [{"level": "ask", "above": "secret"}]},
    "a flow rule with another key": {"flow": [{"level": "ask", "above": "public", "tools": []}]},
    "access that is not an object": {"access": True},
    "access.run that is not a boolean": {"access": {"run": "yes"}},
    "access with another key": {"access": {"run": True, "edit": True}},
    "a binding without a reference": {"bindings": [{"installation": "i", "owner": "user:u"}]},
    "a binding owner of no kind": {"bindings": [{"installation": "i", "owner": "u", "ref": "r"}]},
    "a binding owner of another kind": {"bindings": [{"installation": "i", "owner": "team:t", "ref": "r"}]},
    "a binding owner without an id": {"bindings": [{"installation": "i", "owner": "user:", "ref": "r"}]},
    "pin without a digest": {"pin": {k: v for k, v in _PIN.items() if k != "digest"}},
    "pin with another key": {"pin": {**_PIN, "items": []}},
    "a negative head": {"pin": {**_PIN, "head": -1}},
    "a head that is a boolean": {"pin": {**_PIN, "head": True}},
    "a path nobody knows": {"pin": {**_PIN, "routing": {"path": "engine", "reason": None}}},
    "routing without a reason": {"pin": {**_PIN, "routing": {"path": "legacy"}}},
    "scope without a project": {"pin": {**_PIN, "scope": {"agentKey": "agent-a"}}},
    "scope with another key": {"pin": {**_PIN, "scope": {**_PIN["scope"], "teamId": "t"}}},
    "iat that is not a number": {"iat": "now"},
    "a negative exp": {"exp": -1},
}


@pytest.mark.parametrize("overrides", MALFORMED.values(), ids=MALFORMED.keys())
async def test_a_claim_this_version_cannot_read_is_malformed(overrides: dict[str, Any]) -> None:
    assert await _refused(_verifier(), sign(claims(**overrides))) == "resolved_malformed"


@pytest.mark.parametrize("version", [0, 2, "1", 1.0, True, None], ids=repr)
async def test_a_version_this_verifier_does_not_know_is_refused_as_a_version(version: object) -> None:
    assert await _refused(_verifier(), sign({**claims(), "ver": version})) == "resolved_version"


async def test_a_scope_may_name_the_agent_s_owner() -> None:
    pin = {**_PIN, "scope": {**_PIN["scope"], "agentOwner": "user-9"}}
    assert (await _verifier().verify(sign(claims(pin=pin)), **_for(REQUEST))).pin.scope.agent_owner == "user-9"


@pytest.mark.parametrize("token", ["", "a.b", "a.b.c.d", "!!.b.c", "e30.e30.", "x" * 40])
async def test_a_token_that_is_not_a_compact_jws_is_malformed(token: str) -> None:
    assert await _refused(_verifier(), token) == "resolved_malformed"


def test_the_valid_payload_is_the_contract_s_resolved_turn() -> None:
    """The hand parser and the generated model (what Node signs from) accept the same claims."""
    from app.control_plane.contracts.generated.resolved_turn import (
        ResolvedTurn as ContractTurn,
    )

    contract = ContractTurn.model_validate(VECTOR["valid"]["payload"])
    assert contract.model_dump(mode="json", by_alias=True, exclude_none=False)["cid"] == REQUEST["conversationId"]

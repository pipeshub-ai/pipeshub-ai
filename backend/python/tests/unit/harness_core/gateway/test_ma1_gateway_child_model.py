"""MA1 QA37-I3: an engine child whose role sets a model reaches that model through the model gateway.

An engine never learns which model it runs on: its launch names the neutral ``engine-model`` and the gateway
serves the first route of the calling run's grant. A child run's grant is its own (its spec's ``model.policy``),
so a role-assigned child's ``engine-model`` is its role's model, billed at that model's price into the child's
account, while its parent's token still reaches the parent's. Expected charges are worked from the price rows.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import (
    ENGINE_MODEL_ID,
    ROLE_MODEL_IDS,
    AccessRouteRef,
    ModelApi,
    PinnedRoute,
)
from harness_core.budget.types import Amount
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineVersion,
    GatewayEndpoints,
)
from harness_core.kernel.core.responses import TokenUsage
from harness_core.refusals import HarnessRefusal
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from harness_engines.claude_code import launch as claude_code
from harness_engines.codex import launch as codex
from harness_engines.opencode import launch as opencode
from tests.unit.harness_core.gateway.test_model_gateway import spent
from tests.unit.harness_core.gateway.test_responses_gateway import WIRES

if TYPE_CHECKING:
    from harness_core.gateway.binding import RunAttemptBinding

# gpt-6-sol: 2.00 in, 10.00 out per 1M tokens. claude-haiku-4-5: 1.00 in, 5.00 out.
A = PinnedRoute("openai", "gpt-6-sol", config_key="mk-a", config_model="gpt-6-sol")
B = PinnedRoute("anthropic", "claude-haiku-4-5", config_key="mk-b", config_model="claude-haiku-4-5")
USAGE = TokenUsage(input_tokens=1_000, output_tokens=100)
A_CHARGE = Amount(3_000, 1_100)
B_CHARGE = Amount(1_500, 1_100)


@dataclasses.dataclass
class Family:
    rig: GatewayRig
    to_a: RecordedTransport
    to_b: RecordedTransport
    parent: RunAttemptBinding
    child: RunAttemptBinding


def _family() -> Family:
    rig = GatewayRig()
    to_a = RecordedTransport([reply_events("from A", usage=USAGE)], family="openai")
    to_b = RecordedTransport([reply_events("from B", usage=USAGE)], family="anthropic")
    parent = rig.pin("parent", routes=(A,), transport=to_a)
    child = rig.pin("child", routes=(B,), transport=to_b, parent_run_id="parent")
    return Family(rig, to_a, to_b, parent, child)


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_qa37_i3_a_role_childs_engine_model_is_its_roles_model_billed_at_its_price(wire: str) -> None:
    family = _family()
    codec, body = WIRES[wire]
    assert body()["model"] == ENGINE_MODEL_ID

    _ = await family.rig.gateway.serve(codec, family.rig.token(family.child), {}, body_of(body()))

    assert (len(family.to_b.calls), len(family.to_a.calls)) == (1, 0)
    assert await spent(family.rig, "child") == B_CHARGE


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_qa37_i3_the_parents_token_reaches_the_parents_model_before_and_after_its_child(wire: str) -> None:
    family = _family()
    codec, body = WIRES[wire]

    for binding in (family.parent, family.child, family.parent):
        _ = await family.rig.gateway.serve(codec, family.rig.token(binding), {}, body_of(body()))

    assert (len(family.to_a.calls), len(family.to_b.calls)) == (2, 1)
    assert await spent(family.rig, "child") == B_CHARGE
    # A parent's account holds its subtree: its own two calls at A's price and its child's at B's.
    assert await spent(family.rig, "parent") == Amount(2 * A_CHARGE.usd_micros + B_CHARGE.usd_micros, 3 * 1_100)


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("forged", ["the_parents_run", "the_parents_routes"])
async def test_qa37_i3_a_token_of_one_run_cannot_use_the_others_grant(wire: str, forged: str) -> None:
    family = _family()
    codec, body = WIRES[wire]
    binding = (
        replace(family.child, run_id="parent", parent_run_id=None)
        if forged == "the_parents_run"
        else replace(family.child, routes_digest=family.parent.routes_digest)
    )

    with pytest.raises(HarnessRefusal) as refused:
        _ = await family.rig.gateway.serve(codec, family.rig.token(binding), {}, body_of(body()))

    assert refused.value.reason_code == "binding_mismatch"
    assert family.to_a.calls == [] and family.to_b.calls == []


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("named", [A.model, B.model, "anthropic:claude-haiku-4-5"])
async def test_qa37_i3_an_engine_cannot_name_a_model_only_the_neutral_one(wire: str, named: str) -> None:
    family = _family()
    codec, body = WIRES[wire]

    with pytest.raises(HarnessRefusal):
        _ = await family.rig.gateway.serve(codec, family.rig.token(family.child), {}, body_of(body(model=named)))

    assert family.to_a.calls == [] and family.to_b.calls == []
    assert await spent(family.rig, "child") == Amount(0, 0)


_DIGEST = "sha256:" + "d" * 64
_ENGINES: dict[str, tuple[Any, ModelApi]] = {
    "opencode": (opencode, ModelApi.CHAT_COMPLETIONS),
    "codex": (codex, ModelApi.RESPONSES),
    "claude_code": (claude_code, ModelApi.ANTHROPIC_MESSAGES),
}


def _launch(kind: str, access: AccessRouteRef) -> Any:  # noqa: ANN401
    module, api = _ENGINES[kind]
    endpoints = GatewayEndpoints(
        model_base_url="http://model-gateway:8097/v1",
        model_api=api,
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder="pxh-model-placeholder-0123",
        tools_token_placeholder="pxh-tools-placeholder-4567",
    )
    profile = EngineProfile(engine=EngineVersion(kind, "1.0.0", _DIGEST), mode=EngineMode.BUILD, access=access)
    return module.render_launch(profile, endpoints, "/skills")


def _text(launch: Any) -> str:  # noqa: ANN401
    files = "\n".join(content.decode() for content in launch.files.values())
    return "\n".join([*launch.argv, *launch.env.values(), files])


@pytest.mark.parametrize("kind", sorted(_ENGINES))
def test_qa37_i3_an_engines_launch_names_only_the_neutral_model_whatever_its_runs_route(kind: str) -> None:
    on_a = _launch(kind, AccessRouteRef("openai:gpt-6-sol", "sha256:" + "a" * 64))
    on_b = _launch(kind, AccessRouteRef("anthropic:claude-haiku-4-5", "sha256:" + "b" * 64))

    assert on_a == on_b, "the launch does not depend on the run's model route"
    text = _text(on_b)
    assert ENGINE_MODEL_ID in text
    for name in (A.model, B.model, "gpt-6", "claude-haiku", "claude-sonnet", "claude-opus"):
        assert name not in text


def test_qa37_i3_each_engines_model_settings_are_the_neutral_model() -> None:
    route = AccessRouteRef("anthropic:claude-haiku-4-5", _DIGEST)
    assert {opencode.MODEL, codex.MODEL, claude_code.MODEL} == {ENGINE_MODEL_ID}

    # GA-15: an engine names only neutral ids, one per role it calls; the gateway maps each to its role's route.
    neutral = set(ROLE_MODEL_IDS.values())
    config = json.loads(_launch("opencode", route).env["OPENCODE_CONFIG_CONTENT"])
    assert config["model"] == f"{opencode.PROVIDER}/{ENGINE_MODEL_ID}"
    assert config["small_model"] == f"{opencode.PROVIDER}/{ROLE_MODEL_IDS['fast']}"
    assert [set(provider["models"]) <= neutral for provider in config["provider"].values()] == [True]
    assert {agent.get("model") for agent in config.get("agent", {}).values()} <= {None} | {
        f"{opencode.PROVIDER}/{model_id}" for model_id in neutral
    }

    import tomllib

    codex_config = tomllib.loads(_launch("codex", route).files[codex.CONFIG_PATH].decode())
    assert codex_config["model"] == ENGINE_MODEL_ID
    assert codex.thread_settings("x")["model"] == ENGINE_MODEL_ID

    claude = _launch("claude_code", route)
    model_env = {name: value for name, value in claude.env.items() if name.endswith("_MODEL")}
    assert model_env and set(model_env.values()) <= neutral and claude.env["ANTHROPIC_MODEL"] == ENGINE_MODEL_ID
    assert claude.argv[claude.argv.index("--model") + 1] == ENGINE_MODEL_ID

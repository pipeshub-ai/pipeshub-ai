"""AD-02 (C11.01): the models each coding engine may use are enforced by the model gateway, not only hidden in
the UI. A run's token reaches only its own pinned models (RG-3), and only on the model API its engine speaks,
behind the signed vendor rules, as ``model_gateway_main.guarded_access`` assembles it for production."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.guard import GuardedModelAccess
from harness_core.access.memory import (
    InMemoryOrgAccessPolicies,
    StaticModelAccessResolver,
)
from harness_core.gateway.model_gateway import ChatCompletion
from harness_core.refusals import HarnessRefusal
from harness_core.testing.model_gateway import GatewayRig, body_of
from harness_engines.release import release_model_apis
from harness_engines.vendor_rules import release_vendor_rules
from tests.unit.harness_core.gateway.test_responses_gateway import WIRES

if TYPE_CHECKING:
    from harness_core.access.ports import ResolvedRoute
    from harness_core.access.types import ModelNeed, PinnedRoute

# The wire each released native engine speaks (its adapter's model API).
ENGINE_WIRE = {"opencode": "chat", "codex": "responses", "claude_code": "messages"}


class _GuardedRoutes(StaticModelAccessResolver):
    """The rig's route table behind the production guard: release vendor rules and release engine APIs."""

    def __init__(self) -> None:
        super().__init__()
        self.configured = StaticModelAccessResolver()
        self._guard = GuardedModelAccess(
            self.configured,
            rules=release_vendor_rules,
            policies=InMemoryOrgAccessPolicies(),
            offering="oss",
            engine_apis=release_model_apis(),
        )

    def add(self, tenant: str, route: PinnedRoute, resolved: Any) -> None:  # noqa: ANN401 - the parent's signature
        self.configured.add(tenant, route, resolved)

    async def resolve(
        self, tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: ModelNeed | None = None
    ) -> ResolvedRoute:
        return await self._guard.resolve(tenant, route, run_id=run_id, need=need)


def _rig() -> GatewayRig:
    return GatewayRig(resolver=_GuardedRoutes())


def test_ad02_every_released_engine_has_its_wire_here() -> None:
    assert set(ENGINE_WIRE) == {kind for kind in release_model_apis() if not kind.startswith("acp-")}


@pytest.mark.parametrize(
    ("engine", "wire"),
    [(engine, wire) for engine in sorted(ENGINE_WIRE) for wire in sorted(WIRES) if wire != ENGINE_WIRE[engine]],
)
async def test_ad02_an_engine_run_is_refused_on_a_model_api_its_engine_does_not_speak(engine: str, wire: str) -> None:
    rig = _rig()
    binding = rig.pin(engine=engine)
    codec, body = WIRES[wire]
    with pytest.raises(HarnessRefusal) as refused:
        await rig.gateway.serve(codec, rig.token(binding), {}, body_of(body()))
    assert refused.value.reason_code == "model_api_not_permitted"
    assert rig.transport.calls == []


@pytest.mark.parametrize("model", ["gpt-4o", "claude-sonnet-4-6"])
async def test_ad02_a_model_the_run_was_not_granted_is_refused_whatever_the_client_names(model: str) -> None:
    rig = _rig()
    binding = rig.pin(engine="opencode")
    codec, body = WIRES["chat"]
    with pytest.raises(HarnessRefusal) as refused:
        await rig.gateway.serve(codec, rig.token(binding), {}, body_of(body(model=model)))
    assert refused.value.reason_code == "model_not_granted"
    assert rig.transport.calls == []


async def test_ad02_the_engines_own_api_and_granted_model_are_served() -> None:
    rig = _rig()
    binding = rig.pin(engine="opencode")
    codec, body = WIRES["chat"]
    outcome = await rig.gateway.serve(codec, rig.token(binding), {}, body_of(body()))
    assert isinstance(outcome, ChatCompletion)
    assert len(rig.transport.calls) == 1

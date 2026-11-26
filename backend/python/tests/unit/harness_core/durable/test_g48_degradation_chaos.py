"""G-48 (C20.49), GA-25: the degradation chain with the model provider fully down, on a scripted model (no spend).

G48-3: a knowledge-only prompt still gets its answer: every call to the run's own provider fails (server errors and
timeouts, through every retry), the run moves to the alternate Node pinned for it (``vectors/alternate-models.json``)
and answers there after a knowledge lookup; the move is journaled before the alternate is called, so the chat can
say which model answered. G48-4: with every pinned model down the run ends with the provider's error, never hangs and
never moves to a model admission did not pin. G48-5: a content filter is not an outage, so it never moves to an
alternate Node pinned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import ModelPolicy as SpecModelPolicy
from harness_core.kernel.durable.routing import RoutedModelPort
from harness_core.models.policy import ModelPolicy, ModelPolicyConfig
from harness_core.testing import ErrorSpec, Reply, ScriptedModel
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.run_spec import minimal_run_spec

from .support import World, reply, script

if TYPE_CHECKING:
    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.kernel.durable.ports import DurableModelPort
    from harness_core.models.policy import PolicyRoute

VECTOR = json.loads(
    (
        Path(__file__).resolve().parents[6]
        / "contracts/harness/vectors/alternate-models.json"
    ).read_text("utf-8")
)
PRIMARY, ALTERNATE, SECOND = VECTOR["policy"]


def _spec(*routes: dict[str, Any]) -> RunSpec:
    model = validate_contract(
        SpecModelPolicy, {"policy": list(routes), "promptVersion": "p-test"}
    )
    return minimal_run_spec().model_copy(update={"model": model})


def _down(kind: str) -> Reply:
    return Reply(text="", error=ErrorSpec(kind=kind))  # type: ignore[arg-type]


def _port(provider: str, *replies: Reply) -> ScriptedModelPort:
    return ScriptedModelPort(
        ScriptedModel(script(*replies)), provider=provider, transport="direct"
    )


class _Ports:
    def __init__(self, **ports: DurableModelPort) -> None:
        self.ports = ports

    async def port(self, route: PolicyRoute) -> DurableModelPort:
        return self.ports[route.provider]


async def _run(
    spec: RunSpec, primary: DurableModelPort, **ports: DurableModelPort
) -> tuple[Any, World]:
    world = World()
    world.admit(spec=spec)

    async def sleep(_: float) -> None:
        return None

    routed = RoutedModelPort(
        ModelPolicy.from_spec(spec.model), primary, _Ports(**ports)
    )
    done = await world.run(
        world.kernel(routed, model_policy=ModelPolicyConfig(max_retries=2), sleep=sleep)
    )  # type: ignore[arg-type]
    return done, world


def test_g48_vector_is_an_outage_only_and_the_worker_reads_every_trigger() -> None:
    policy = ModelPolicy.from_spec(_spec(PRIMARY, ALTERNATE, SECOND).model)
    assert [r.provider for r in policy.routes] == ["openai", "anthropic", "azureopenai"]
    for trigger in ("5xx", "timeout", "429_exhausted"):
        assert policy.next_route(policy.primary, trigger) == policy.routes[1]
    assert policy.next_route(policy.primary, "content_filter") is None


async def test_g48_3_ga25_provider_fully_down_a_knowledge_prompt_still_answers_on_the_alternate() -> (
    None
):
    spec = _spec(PRIMARY, ALTERNATE)
    down = _port(
        "openai", _down("server_error"), _down("timeout"), _down("server_error")
    )
    alternate = _port(
        "anthropic",
        reply("", ("lookup", {"q": "refund policy"})),
        reply("Refunds take 5 days."),
    )
    done, world = await _run(spec, down, anthropic=alternate)
    assert done.status == "completed"
    assert len(down.calls) == 3, "the run's own provider is retried, then left"
    assert len(alternate.calls) == 2, "the lookup and the answer, both on the alternate"
    (fallback,) = world.events("model.fallback")
    assert fallback.data["to"] == {"provider": "anthropic", "model": "claude-x"}
    types = world.types()
    assert types.index("model.fallback") < types.index("model.request"), (
        "journaled before the alternate is called"
    )
    assert world.events("run.failed") == []


async def test_g48_4_every_pinned_model_down_ends_with_the_providers_error_and_never_leaves_the_pin() -> (
    None
):
    spec = _spec(PRIMARY, ALTERNATE)
    down = _port("openai", *(_down("server_error") for _ in range(3)))
    also_down = _port("anthropic", *(_down("server_error") for _ in range(3)))
    unpinned = _port("azureopenai", reply("never"))
    done, world = await _run(spec, down, anthropic=also_down, azureopenai=unpinned)
    assert done.status == "failed"
    assert world.events("run.failed")[0].data["errorType"] == "server_error"
    assert unpinned.calls == []
    assert len(world.events("model.fallback")) == 1


async def test_g48_5_a_content_filter_is_not_an_outage_and_never_moves_to_an_alternate() -> (
    None
):
    spec = _spec(PRIMARY, ALTERNATE)
    filtered = _port("openai", _down("content_filter"))
    alternate = _port("anthropic", reply("never"))
    done, world = await _run(spec, filtered, anthropic=alternate)
    assert done.status == "failed" and alternate.calls == []
    assert world.events("model.fallback") == []

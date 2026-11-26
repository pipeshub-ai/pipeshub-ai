"""P4.3 RT-1..RT-3 and P4.7 MP-3..MP-10: retries and fallback over the run's pinned routes, in the durable kernel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.events.model_fallback import ModelFallbackEvent
from harness_core.contracts.generated.run_spec import ModelPolicy as SpecModelPolicy
from harness_core.failpoints import Raise
from harness_core.journal.children import child_spec
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ProviderState,
    ThinkingPart,
    ToolMessage,
)
from harness_core.kernel.durable.routing import RoutedModelPort
from harness_core.kernel.fold import fold_run
from harness_core.models.errors import ModelRouteNotPinned
from harness_core.models.policy import ModelPolicy, ModelPolicyConfig, PolicyRoute
from harness_core.testing import ErrorSpec, Reply, ScriptedModel, ToolCallSpec, armed
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.run_spec import minimal_run_spec

from .support import RUN, Crash, World, reply, script

if TYPE_CHECKING:
    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.kernel.durable.ports import DurableModelPort, SampledTurn

A = {"provider": "a", "model": "a-1"}
B = {"provider": "b", "model": "b-1"}
C = {"provider": "c", "model": "c-1"}
ALL = ["5xx", "timeout", "content_filter", "429_exhausted"]


def _spec(*routes: dict[str, Any]) -> RunSpec:
    base = minimal_run_spec()
    model = validate_contract(SpecModelPolicy, {"policy": list(routes), "promptVersion": "p-test"})
    return base.model_copy(update={"model": model})


def _error(kind: str, text: str = "", **extra: Any) -> Reply:  # noqa: ANN401
    return Reply(text=text, chunks=[text] if text else None, error=ErrorSpec(kind=kind, **extra))  # type: ignore[arg-type]


class StatefulPort(ScriptedModelPort):
    """A scripted route whose answers carry its own provider state, as a real wire's do."""

    def _sampled(self, event: Any) -> SampledTurn:  # noqa: ANN401
        sampled = super()._sampled(event)
        message = sampled.message.model_copy(
            update={"provider_state": ProviderState(wire=f"{self.provider}_wire", blocks=[{"sig": self.provider}])}
        )
        return type(sampled)(**{**sampled.__dict__, "message": message, "raw": message.model_dump_json().encode()})


def _port(provider: str, *replies: Reply) -> StatefulPort:
    return StatefulPort(ScriptedModel(script(*replies)), provider=provider, transport="direct")


class Ports:
    """Builds a fallback route's port by provider, and counts what it built."""

    def __init__(self, **ports: DurableModelPort) -> None:
        self.ports = ports
        self.built: list[str] = []

    async def port(self, route: PolicyRoute) -> DurableModelPort:
        self.built.append(route.provider)
        return self.ports[route.provider]


def _sleeps(delays: list[float]) -> Any:  # noqa: ANN401
    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    return sleep


def _routed(spec: RunSpec, primary: DurableModelPort, ports: Ports, *, extra: tuple[dict[str, Any], ...] = ()) -> RoutedModelPort:
    """The port as an assembler builds it; ``extra`` routes model an assembler that offers more than the pin."""
    pinned = ModelPolicy.from_spec(spec.model)
    if extra:
        pinned = ModelPolicy.from_spec(_spec(*(r.model_dump(exclude_none=True) for r in spec.model.policy), *extra).model)
    return RoutedModelPort(pinned, primary, ports)


async def _run(world: World, spec: RunSpec, primary: DurableModelPort, ports: Ports, **kernel: Any) -> Any:  # noqa: ANN401
    delays: list[float] = []
    kernel.setdefault("sleep", _sleeps(delays))
    routed = kernel.pop("routed", None) or _routed(spec, primary, ports)
    disposition = await world.run(world.kernel(routed, model_policy=ModelPolicyConfig(max_retries=2), **kernel))  # type: ignore[arg-type]
    return disposition, delays


def _world(spec: RunSpec) -> World:
    world = World()
    world.admit(spec=spec)
    return world


def _texts(world: World, attempt: int) -> list[str]:
    return [str(f.data["text"]) for f in world.live.frames if f.model_attempt == attempt and f.kind == "text_delta"]


async def test_rt1_rt2_a_stream_cut_mid_way_is_retried_as_a_new_attempt_without_duplicate_deltas() -> None:
    spec = _spec(A)
    world = _world(spec)
    a = _port("a", _error("server_error", "Hel"), Reply(text="Hello", chunks=["Hel", "lo"]))
    done, delays = await _run(world, spec, a, Ports())
    assert done.status == "completed"
    assert "".join(_texts(world, 1)) == "Hel"
    assert "".join(_texts(world, 2)) == "Hello"
    for attempt in (1, 2):
        ns = [f.n for f in world.live.frames if f.model_attempt == attempt]
        assert ns == list(range(1, len(ns) + 1)), "gap-free, never repeated, within an attempt"
    (request,) = world.events("model.request")
    assert request.data["modelAttempt"] == 2
    assert len(world.events("model.response")) == 1
    assert a.calls[0] == a.calls[1], "the retry sends the same request"
    assert delays == [1.0]
    assert world.events("model.fallback") == []


async def test_rt3_retries_are_bounded_then_the_run_fails_as_today() -> None:
    spec = _spec(A)
    world = _world(spec)
    a = _port("a", *(_error("server_error") for _ in range(3)))
    done, delays = await _run(world, spec, a, Ports())
    assert (done.status, len(a.calls), delays) == ("failed", 3, [1.0, 2.0])
    assert world.events("run.failed")[0].data["errorType"] == "server_error"


async def test_mp3_5xx_falls_back_journaled_before_the_new_routes_request() -> None:
    spec = _spec(A, {**B, "on": ["5xx"]})
    world = _world(spec)
    a = _port("a", *(_error("server_error") for _ in range(3)))
    b = _port("b", reply("from b"))
    done, _ = await _run(world, spec, a, Ports(b=b))
    assert done.status == "completed" and len(a.calls) == 3 and len(b.calls) == 1
    types = world.types()
    assert types.index("model.fallback") < types.index("model.request")
    (fallback,) = world.events("model.fallback")
    assert fallback.data == {"from": A, "to": B, "reason": "5xx"} and fallback.critical is False
    validate_contract(ModelFallbackEvent, fallback.envelope())
    assert world.events("model.request")[0].data["route"] == {"provider": "b", "model": "scripted"}
    assert fold_run(world.events(), run_id=RUN).model_route is not None


@pytest.mark.parametrize(("kind", "reason", "a_calls"), [("content_filter", "content_filter", 1), ("timeout", "timeout", 3)])
async def test_mp4_mp5_content_filter_falls_back_at_once_timeout_after_retries(kind: str, reason: str, a_calls: int) -> None:
    spec = _spec(A, {**B, "on": ALL})
    world = _world(spec)
    a = _port("a", *(_error(kind) for _ in range(3)))
    b = _port("b", reply("from b"))
    done, _ = await _run(world, spec, a, Ports(b=b))
    assert done.status == "completed" and len(a.calls) == a_calls
    assert [e.data["reason"] for e in world.events("model.fallback")] == [reason]


async def test_mp5_a_rate_limit_falls_back_instead_of_yielding_only_when_a_route_takes_it() -> None:
    spec = _spec(A, {**B, "on": ["429_exhausted"]})
    world = _world(spec)
    a = _port("a", _error("rate_limited", retry_after_s=60))
    b = _port("b", reply("from b"))
    done, _ = await _run(world, spec, a, Ports(b=b))
    assert done.status == "completed"
    assert [e.data["reason"] for e in world.events("model.fallback")] == ["429_exhausted"]

    spec2 = _spec(A, {**B, "on": ["5xx"]})
    world2 = _world(spec2)
    a2 = _port("a", _error("rate_limited", retry_after_s=60))
    done2, _ = await _run(world2, spec2, a2, Ports(b=_port("b", reply("never"))))
    assert done2.status == "yielded" and world2.events("model.fallback") == []


async def test_mp6_only_pinned_routes_are_ever_sampled() -> None:
    spec = _spec(A)
    world = _world(spec)
    a = _port("a", _error("content_filter"))
    c = _port("c", reply("never"))
    ports = Ports(c=c)
    routed = _routed(spec, a, ports, extra=({**C, "on": ALL},))
    done, _ = await _run(world, spec, a, ports, routed=routed)
    assert done.status == "failed" and c.calls == [] and ports.built == []
    assert world.events("model.fallback") == []
    unpinned = PolicyRoute(index=1, provider="c", model="c-1", route_id="c:c-1", on=frozenset(ALL))
    with pytest.raises(ModelRouteNotPinned):
        await RoutedModelPort(ModelPolicy.from_spec(spec.model), a, ports).select(unpinned)


def test_mp7_a_child_granted_one_route_of_its_parents_two_has_no_fallback() -> None:
    parent = _spec(A, {**B, "on": ["5xx"]})
    grants: dict[str, Any] = {
        "access": ["a:a-1"], "tools": [], "sandboxProfile": "none", "egress": "none",
        "maxTurns": 4, "maxTokens": 1000, "maxChildren": 0,
    }
    child = minimal_run_spec().model_validate(child_spec(parent, grants))
    policy = ModelPolicy.from_spec(child.model)
    assert [r.provider for r in policy.routes] == ["a"]
    assert policy.next_route(policy.primary, "5xx") is None


async def test_mp8_a_resume_after_the_fallback_commit_goes_straight_to_the_new_route() -> None:
    spec = _spec(A, {**B, "on": ["content_filter"]})
    world = _world(spec)
    a = _port("a", _error("content_filter"))
    b = _port("b", reply("from b"))
    with armed({"worker.model.after_fallback_commit": Raise(Crash)}):
        try:
            await _run(world, spec, a, Ports(b=b))
        except Crash:
            pass
        else:
            raise AssertionError("expected the worker to crash")
    world.expire()
    a_again = _port("a", reply("never"))
    done, _ = await _run(world, spec, a_again, Ports(b=b))
    assert done.status == "completed"
    assert a_again.calls == [] and len(b.calls) == 1
    assert len(world.events("model.fallback")) == 1
    route = fold_run(world.events(), run_id=RUN).model_route
    assert route is not None and (route.provider, route.model) == ("b", "b-1")


async def test_mp9_the_new_route_never_receives_the_old_providers_state() -> None:
    spec = _spec(A, {**B, "on": ["5xx"]})
    world = _world(spec)
    a = _port(
        "a",
        Reply(text="looking", reasoning="a's chain", tool_calls=[ToolCallSpec(id="toolu.01/x", name="lookup", args={"q": "x"})]),
        *(_error("server_error") for _ in range(3)),
    )
    b = _port("b", reply("", ("lookup", {"q": "y"})), reply("done"))
    done, _ = await _run(world, spec, a, Ports(b=b))
    assert done.status == "completed" and len(b.calls) == 2
    for call in b.calls:
        assistants = [m for m in call.messages if isinstance(m, AssistantMessage)]
        a_turn = assistants[0]
        assert a_turn.provider_state is None, "A's state never reaches B"
        assert not any(isinstance(p, ThinkingPart) for p in a_turn.content)
        ids = {c.id for c in a_turn.tool_calls or []}
        results = {m.tool_call_id for m in call.messages if isinstance(m, ToolMessage)}
        assert "toolu.01/x" not in ids and ids <= results, "the id is rewritten identically in the call and its result"
    b_turn = [m for m in b.calls[1].messages if isinstance(m, AssistantMessage)][1]
    assert b_turn.provider_state == ProviderState(wire="b_wire", blocks=[{"sig": "b"}]), "B's own state comes back"


async def test_mp10_no_route_left_fails_with_the_original_error_type() -> None:
    spec = _spec(A, {**B, "on": ["5xx"]})
    world = _world(spec)
    a = _port("a", _error("content_filter"))
    done, _ = await _run(world, spec, a, Ports(b=_port("b", reply("never"))))
    assert done.status == "failed"
    assert world.events("run.failed")[0].data["errorType"] == "content_filter"
    assert world.events("model.fallback") == []


async def test_st10_without_a_policy_a_transient_failure_is_retried_on_the_one_route() -> None:
    world = _world(minimal_run_spec())
    port = ScriptedModelPort(ScriptedModel(script(*(_error("server_error") for _ in range(3)))))
    delays: list[float] = []
    done = await world.run(world.kernel(port, sleep=_sleeps(delays)))
    assert (done.status, len(port.calls), delays) == ("failed", 3, [1.0, 2.0])
    assert world.events("run.failed")[0].data["errorType"] == "server_error"
    assert world.events("model.fallback") == []


async def test_st10_without_a_policy_a_retry_that_answers_completes_the_run() -> None:
    world = _world(minimal_run_spec())
    port = ScriptedModelPort(ScriptedModel(script(_error("server_error"), reply("fine"))))
    done = await world.run(world.kernel(port, sleep=_sleeps([])))
    assert done.status == "completed" and len(port.calls) == 2
    (request,) = world.events("model.request")
    assert request.data["modelAttempt"] == 2


async def test_without_a_policy_a_failure_that_is_not_transient_is_not_retried() -> None:
    world = _world(minimal_run_spec())
    port = ScriptedModelPort(ScriptedModel(script(_error("content_filter"))))
    done = await world.run(world.kernel(port, sleep=_sleeps([])))
    assert done.status == "failed" and len(port.calls) == 1

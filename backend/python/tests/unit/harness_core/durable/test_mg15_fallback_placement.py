"""MG-15 (C3.68) in the durable kernel: a fallback route outside the org's region pin is skipped, never called.

FR-1 a refused fallback is passed over for the next pinned route that takes the trigger; the move is journaled to
     the compliant route only.
FR-2 with no compliant route left, the run fails as it would with no fallback, and the refused route is never
     sampled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.access.errors import ModelNotAllowed

from .test_model_policy import A, B, C, Ports, _error, _port, _run, _spec, _world, reply

if TYPE_CHECKING:
    from harness_core.models.policy import PolicyRoute


class PlacedPorts(Ports):
    """Route ports whose resolution refuses the routes outside the pin, as a placement rule's ``fallback_model`` does."""

    def __init__(self, outside: set[str], **ports: object) -> None:
        super().__init__(**ports)  # type: ignore[arg-type]
        self.outside = outside

    async def port(self, route: PolicyRoute) -> object:  # type: ignore[override]
        if route.provider in self.outside:
            self.built.append(f"refused:{route.provider}")
            raise ModelNotAllowed("a rule of the organization does not allow this model")
        return await super().port(route)


async def test_mg15_fr1_a_fallback_outside_the_pin_is_skipped_for_the_next_compliant_route() -> None:
    spec = _spec(A, {**B, "on": ["5xx"]}, {**C, "on": ["5xx"]})
    world = _world(spec)
    a = _port("a", *(_error("server_error") for _ in range(3)))
    b = _port("b", reply("never"))
    c = _port("c", reply("from c"))
    ports = PlacedPorts({"b"}, b=b, c=c)

    done, _ = await _run(world, spec, a, ports)

    assert done.status == "completed"
    assert b.calls == [] and len(c.calls) == 1
    assert ports.built == ["refused:b", "c"]
    assert [e.data["to"] for e in world.events("model.fallback")] == [C]


async def test_mg15_fr2_with_no_compliant_route_the_run_fails_and_nothing_outside_is_called() -> None:
    spec = _spec(A, {**B, "on": ["5xx"]})
    world = _world(spec)
    a = _port("a", *(_error("server_error") for _ in range(3)))
    b = _port("b", reply("never"))

    done, _ = await _run(world, spec, a, PlacedPorts({"b"}, b=b))

    assert done.status == "failed" and b.calls == []
    assert world.events("model.fallback") == []
    assert world.events("run.failed")[0].data["errorType"] == "server_error"

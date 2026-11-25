"""WP100-FB-2b (G-48, C20.49), GA-25 on the shipped default: with nothing set, the worker's assembly gives the
kernel the routed port over the pinned alternates, so a knowledge prompt with its provider fully down still answers.

GA-25's chaos test (``test_g48_degradation_chaos``) builds the routed port itself; this one builds it the way a run
is assembled (``build_model_policy``), with ``HARNESS_MODEL_FALLBACK`` unset, as every shipped config leaves it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.harness.assembly.model_policy import build_model_policy
from harness_core.kernel.durable.routing import RoutedModelPort
from tests.unit.harness_core.durable.support import World, reply
from tests.unit.harness_core.durable.test_g48_degradation_chaos import (
    ALTERNATE,
    PRIMARY,
    _down,  # pyright: ignore[reportPrivateUsage]
    _port,  # pyright: ignore[reportPrivateUsage]
    _Ports,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    import pytest


async def test_wp100_fb_2b_ga25_the_shipped_default_answers_on_the_alternate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESS_MODEL_FALLBACK", raising=False)
    spec = _spec(PRIMARY, ALTERNATE)
    down = _port("openai", _down("server_error"), _down("timeout"), _down("server_error"))
    alternate = _port("anthropic", reply("", ("lookup", {"q": "refund policy"})), reply("Refunds take 5 days."))
    model: Any = SimpleNamespace(port=down)
    port, config = build_model_policy(spec.model, model, lambda: _Ports(anthropic=alternate))  # type: ignore[arg-type,return-value]
    assert isinstance(port, RoutedModelPort) and config is not None

    async def sleep(_: float) -> None:
        return None

    world = World()
    world.admit(spec=spec)
    done = await world.run(world.kernel(port, model_policy=config, sleep=sleep))  # type: ignore[arg-type]
    assert done.status == "completed"
    (fallback,) = world.events("model.fallback")
    assert fallback.data["to"] == {"provider": "anthropic", "model": "claude-x"}

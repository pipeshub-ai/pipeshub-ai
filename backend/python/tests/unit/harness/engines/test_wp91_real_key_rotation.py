"""C6.17 / OP-08 (WP-91) on the real lane: an org model key rotated mid-turn needs no sandbox change.

The real Codex binary in its release image, behind the real egress proxy and E3's real ``ModelGateway``, works one
turn of two model calls. Between them the org's key is rotated at the gateway's resolver (the admin saves a new key;
``test_wp91_key_rotation.py`` holds that ``ConfiguredModelAccessResolver`` reads it at the next call). The provider
gets the old key on the first call and the new one on the second; the engine's process, its sandbox and the
placeholder it sends are the same throughout, and nothing was acquired again.

- WP91-ROT-4: the turn completes in the one sandbox it started in, the provider saw the old key then the new one,
  every gateway request carried the same placeholder token, and no request was refused.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from harness_core.models.transports.openai import OpenAITransport
from harness_core.testing.model_gateway import org_key_route
from tests.unit.harness.engines.real_stack import PROVIDER_KEY
from tests.unit.harness.engines.test_real_codex_controller import (
    MODEL_KEY,
    TENANT,
    Lane,
    codex_broker,
    model_gateway,
    real_stack,
)

if TYPE_CHECKING:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import ModelGatewayLane, RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]
__all__ = ["codex_broker", "model_gateway", "real_stack"]

ROTATED_KEY = "org-key-rotated-mid-turn-0123456789"


async def test_wp91_rot_4_a_key_rotated_between_two_model_calls_of_one_turn_needs_no_sandbox_change(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    lane = Lane(stack, codex_broker, model_gateway)
    run = lane.run(1)
    gateway = lane.gateway(1)
    acquired: list[str] = []
    acquire = codex_broker.acquire

    async def counted(spec, **kwargs):  # type: ignore[no-untyped-def] # noqa: ANN202
        handle = await acquire(spec, **kwargs)
        acquired.append(handle.id)
        return handle

    codex_broker.acquire = counted  # type: ignore[method-assign]
    turn = asyncio.ensure_future(
        lane.controller.run_turn(run, lane.task(0, 'hold\ntool search {"q": "rotation"}\nsay done'))
    )
    await asyncio.wait_for(gateway.holding.wait(), 300)
    # The first model call is at the provider with the key in force when it was made; the admin now rotates it.
    base = f"{stack.model_base}/m/{MODEL_KEY}/v1"
    model_gateway.resolver.add(
        TENANT, lane.route, org_key_route(lane.route, OpenAITransport(api_key=ROTATED_KEY, model=lane.route.model, base_url=base))
    )
    gateway.release()
    outcome = await asyncio.wait_for(turn, 600)

    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done")
    seen = stack.models.authorizations[MODEL_KEY]
    assert seen[0] == f"Bearer {PROVIDER_KEY}"
    assert seen[-1] == f"Bearer {ROTATED_KEY}"
    assert seen.index(f"Bearer {ROTATED_KEY}") > 0
    # One sandbox for the turn: nothing was acquired again for the new key.
    assert len(acquired) == 1
    # What left the sandbox for the gateway was the same placeholder-swapped run token on every request.
    tokens = {r.authorization for r in model_gateway.requests}
    assert len(tokens) == 1 and ROTATED_KEY not in next(iter(tokens)) and PROVIDER_KEY not in next(iter(tokens))
    assert model_gateway.refused == []
    assert len(gateway.model_calls) >= 2

"""G2 B-1, B-2: an engine cannot spend past its run's cap, nor on another run's account by naming that run.

The real ``ModelGateway`` over E3's in-memory rig (``GatewayRig``: ``InMemoryBudgetLedger``, a recording
transport), on both wires. The account a call is charged to comes from the gateway token alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.errors import BudgetExhausted
from harness_core.access.types import RunBudget
from harness_core.budget.types import Amount
from harness_core.kernel.core.responses import TokenUsage
from harness_core.testing.model_gateway import GatewayRig, reply_events
from tests.unit.harness_core.gateway.test_model_gateway import prespend, spent
from tests.unit.harness_core.gateway.test_responses_gateway import WIRES, respond

if TYPE_CHECKING:
    from harness_core.kernel.core.streaming import StreamEvent


async def test_g2_b1_an_account_spent_by_its_own_calls_refuses_the_next_on_both_wires() -> None:
    rig = GatewayRig()
    events: list[StreamEvent] = [reply_events("big", usage=TokenUsage(input_tokens=1_000, output_tokens=1_000))]
    rig.transport.events = events
    a = rig.pin("run-a", budget=RunBudget(max_cost_usd=0.01, max_tokens=None))
    chat_wire, chat_body = WIRES["chat"]
    _ = await respond(rig, rig.token(a), chat_body(), wire=chat_wire)
    committed = await spent(rig, "run-a")
    for wire in ("chat", "responses"):
        codec, body = WIRES[wire]
        with pytest.raises(BudgetExhausted) as refused:
            await respond(rig, rig.token(a), body(), wire=codec)
        assert (refused.value.reason_code, refused.value.http_status) == ("budget_exhausted", 402)
    assert await spent(rig, "run-a") == committed
    assert len(rig.transport.calls) == 1


CLAIMS_OF_B: list[tuple[str, dict[str, Any], dict[str, str]]] = [
    ("user", {"user": "run-b"}, {}),
    ("metadata.runId", {"metadata": {"runId": "run-b"}}, {}),
    ("X-Run-Id", {}, {"X-Run-Id": "run-b"}),
    ("OpenAI-Project", {}, {"OpenAI-Project": "run-b"}),
]


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize(("where", "extra", "headers"), CLAIMS_OF_B, ids=[c[0] for c in CLAIMS_OF_B])
async def test_g2_b2_naming_another_run_never_moves_the_spend_to_it(
    wire: str, where: str, extra: dict[str, Any], headers: dict[str, str]
) -> None:
    rig = GatewayRig()
    a = rig.pin("run-a", budget=RunBudget(max_cost_usd=1.0, max_tokens=None))
    b = rig.pin("run-b")
    codec, body = WIRES[wire]
    _ = await respond(rig, rig.token(b), body(), wire=codec)
    b_before = await spent(rig, "run-b")
    await prespend(rig, Amount(1_000_000, 10), run_id="run-a")
    a_before = await spent(rig, "run-a")
    with pytest.raises(BudgetExhausted) as refused:
        await respond(rig, rig.token(a), body(**extra), headers, wire=codec)
    assert refused.value.reason_code == "budget_exhausted", where
    assert await spent(rig, "run-b") == b_before
    assert await spent(rig, "run-a") == a_before
    assert len(rig.transport.calls) == 1

"""Round 11 item E (WP-49 x WP-41): a model call is metered once.

R11-MS-1 a model the run already metered (``RunChatModel``) passes through the helper scope unchanged: a helper that
         hands the run's model on to ``app.utils.llm`` is never metered a second time.
R11-MS-2 the quarantined summarizer's calls, over the run's metered and admitted model and inside the helper scope a
         tool call runs in, take exactly one ledger reservation and one provider slot per provider call.
R11-MS-3 a helper model the scope builds is charged once per call, however often the helper asks the scope for it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.harness.assembly.model import admitted, helper_model_scope, metered
from app.harness.quarantine import quarantined_summarizer
from app.harness.run_chat_model import RunChatModel
from app.utils.run_model_scope import run_model_scope, scoped_helper_model
from harness_core.testing.ids import SeededIds
from tests.unit.harness.test_wp49_meter_all import (
    PROVIDER,
    RUN,
    ProviderModel,
    UnmeteredCallError,
    _guard,
    _meter,
    _resolved,
    _Run,
    _run_model,
    _World,
    world,
)

__all__ = ["world"]

SUMMARY = json.dumps({"summary": "the policy changed", "points": [], "instructions_seen": False})


class _Summarizing(ProviderModel):
    """The provider, answering in the summary's shape, and only inside the run's reservation and slot."""

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any  # noqa: ANN401
    ) -> ChatResult:
        if not self.world.admitted_and_metered():
            raise UnmeteredCallError("the summarizer reached the provider outside the run's transport")
        self.world.calls += 1
        usage = {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=SUMMARY, usage_metadata=usage))])


def _config() -> dict[str, Any]:
    return {"provider": PROVIDER, "modelKey": "model-1", "configuration": {"model": "gpt-x"}}


def test_r11_ms1_the_runs_own_model_is_not_metered_again(world: _World) -> None:
    metered_calls: list[str] = []
    meter = _meter(world)

    def counting(model: Any, name: str) -> Any:  # noqa: ANN401
        metered_calls.append(name)
        return meter(model, name)

    chat = _run_model(world).chat_model()
    assert isinstance(chat, RunChatModel)
    with run_model_scope(helper_model_scope(_Run(), counting, _guard())):  # type: ignore[arg-type]
        assert scoped_helper_model(chat, _config()) is chat
    assert metered_calls == []


def test_r11_ms2_the_summarizers_calls_are_reserved_and_admitted_once_each(world: _World) -> None:
    # The run's model as the assembler hands it on (admitted, then metered), over a provider that summarizes.
    run_model, _ = metered(
        admitted(_resolved(world, _Summarizing(world=world)), world.gate, "background", None),
        accounts=world.ledger, account=RUN, pricing_model="gpt-x", ids=SeededIds(0),
    )
    summarizer = quarantined_summarizer(run_model, model_id="gpt-x")
    assert summarizer is not None

    async def summarize() -> None:
        # Inside a tool call: the helper scope is bound, as RegistryToolPort(around=...) binds it.
        with run_model_scope(helper_model_scope(_Run(), _meter(world), _guard())):  # type: ignore[arg-type]
            batches = ["first batch " * 50, "second batch " * 50]
            summary = await summarizer.summarize(batches, request="what changed", sources=("r1",))
            assert summary.summary == "the policy changed"

    asyncio.run(summarize())
    assert world.calls >= 1
    assert world.ledger.reserved == world.calls, "one reservation per provider call, never two"
    assert world.ledger.committed == world.calls
    assert world.ledger.open == 0
    assert world.gate.in_flight("openai") == 0


def test_r11_ms3_a_helpers_model_is_charged_once_per_call(world: _World) -> None:
    async def helper() -> None:
        with run_model_scope(helper_model_scope(_Run(), _meter(world), _guard())):  # type: ignore[arg-type]
            built = scoped_helper_model(_resolved(world).raw_llm, _config())
            # A helper that asks the scope again for the model it already holds gets the same model back.
            again = scoped_helper_model(built, _config())
            assert again is built
            _ = await again.ainvoke([HumanMessage(content="describe the table")])

    asyncio.run(helper())
    assert world.calls == 1
    assert world.ledger.reserved == 1 and world.ledger.committed == 1


"""WP-62 (WP62-STALL-1..4): an engine's stall rounds are its model's turns, and a spent web allowance is no stall.

Found in a manual test: Codex ran a batch script that made six ``fetch_url`` calls in parallel through
``ph tools call``. Each came back ``budget_exhausted``. The controller counted every gateway call as a round, so the
sixth refusal made six "stale rounds" and stopped the turn (``engine.turn.limit reasonCode=limit_stall``) 0.06 s
later, before Codex's model had read any of them. A round is now the calls one model turn made, weighed once the
model has made its next call; a round of allowance refusals counts neither as stale nor as progress.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness_core.effects.types import ToolExecution
from harness_core.engines.controller import STOPPING_TEXT
from harness_core.kernel.core.stall import STALL_REPEAT_ROUNDS, STALL_STALE_ROUNDS
from harness_core.testing.engines.durable import SESSION, EngineWorld

if TYPE_CHECKING:
    from harness_core.effects.types import ToolContext
    from harness_core.kernel.core.messages import ToolCall

RUN = "run-wp62-rounds"
# The six refusals that batch got, as the web tool words them.
REFUSAL = json.dumps(
    {
        "ok": False,
        "error_type": "budget_exhausted",
        "error": "The page reads for this answer are used up; answer with what you have read",
        "retryable": False,
    },
    indent=2,
)
BLOCKED = json.dumps(
    {"ok": False, "error_type": "blocked", "error": "DuckDuckGo blocked the search as automated traffic", "retryable": True}
)


def _returning(text: str):  # noqa: ANN202
    async def handler(call: ToolCall, ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="error", content=text)

    return handler


def _batch(n: int, prefix: str) -> str:
    return "tools lookup " + json.dumps([{"url": f"https://{prefix}.example/{i}"} for i in range(n)])


def _completed(world: EngineWorld) -> list[dict[str, object]]:
    return [dict(e.data) for e in world.journal.events(SESSION) if e.type == "run.completed"]


def _answer(world: EngineWorld) -> str:
    texts = [e for e in world.journal.events(SESSION) if e.type == "message.completed" and e.data.get("isFinal")]
    assert texts, "no final message"
    return world.content.get_sync(texts[-1].data["textRef"]).decode()  # type: ignore[arg-type]


async def test_wp62_stall_1_one_parallel_batch_of_six_refusals_is_one_round_and_the_engine_finishes() -> None:
    """The recorded failure: six allowance refusals in one batch, then the model writes its report."""
    world = EngineWorld(metered=True, handlers={"lookup": _returning(REFUSAL)})
    world.admit(RUN, "\n".join([_batch(STALL_STALE_ROUNDS, "pages"), "say Report written from the pages already read"]))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed", disposition
    assert sum(world.effects.values()) == STALL_STALE_ROUNDS
    assert _completed(world) == [{"outcome": "answered"}]


async def test_wp62_stall_1_one_parallel_batch_of_six_failures_is_one_round() -> None:
    world = EngineWorld(metered=True, handlers={"lookup": _returning(BLOCKED)})
    world.admit(RUN, "\n".join([_batch(STALL_STALE_ROUNDS + 2, "search"), "say done"]))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed", disposition
    assert sum(world.effects.values()) == STALL_STALE_ROUNDS + 2
    assert _completed(world) == [{"outcome": "answered"}]


async def test_wp62_stall_2_turn_after_turn_of_allowance_refusals_is_no_stall() -> None:
    """The model is told the allowance is spent; it is not stopped for being told so, whatever it tries next."""
    world = EngineWorld(metered=True, handlers={"lookup": _returning(REFUSAL)})
    turns = [f'tool lookup {{"url": "https://pages.example/{i}"}}' for i in range(STALL_STALE_ROUNDS + 3)]
    world.admit(RUN, "\n".join([*turns, "say done"]))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed", disposition
    assert sum(world.effects.values()) == STALL_STALE_ROUNDS + 3
    assert _completed(world) == [{"outcome": "answered"}]


async def test_wp62_stall_3_model_turns_whose_calls_all_fail_still_stop_the_engine() -> None:
    """The stale rule holds per model turn: the turn after the sixth failed one is refused and the run ends partial."""
    world = EngineWorld(metered=True, handlers={"lookup": _returning(BLOCKED)})
    turns = [f'tool lookup {{"url": "https://search.example/{i}"}}' for i in range(STALL_STALE_ROUNDS + 3)]
    world.admit(RUN, "\n".join([*turns, "say done"]))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed", disposition
    assert sum(world.effects.values()) == STALL_STALE_ROUNDS
    assert _completed(world) == [{"outcome": "answered", "truncatedBy": "stall"}]


async def test_wp62_stall_4_a_call_after_the_stall_is_answered_not_run() -> None:
    world = EngineWorld(metered=True)
    world.admit(RUN, "\n".join(['tool lookup {"jql": "project = PP"}'] * (STALL_REPEAT_ROUNDS + 3) + ["say done"]))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed", disposition
    assert sum(world.effects.values()) == STALL_REPEAT_ROUNDS
    refused = [
        e for e in world.journal.events(SESSION) if e.type == "tool.outcome" and e.data.get("status") == "refused"
    ]
    # The refusal is the controller's, before any effect: nothing about it is journaled as a tool's outcome.
    assert refused == []
    assert STOPPING_TEXT.startswith("Not run")

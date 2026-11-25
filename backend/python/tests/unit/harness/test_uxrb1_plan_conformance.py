"""UXR-B1 B1-7: one fixture task gives the same plan events on the loop and the three engines (``plan-steps.json``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.assembly.plan import PlanRoutes
from harness_core.engines.events import PlanUpdated
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.task_ledger import TaskLedger
from harness_engines.claude_code.mapper import ClaudeEventMapper
from harness_engines.codex.mapper import CodexEventMapper
from harness_engines.opencode.mapper import OpencodeEventMapper

if TYPE_CHECKING:
    from collections.abc import Callable

VECTOR = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts" / "harness" / "vectors" / "plan-steps.json").read_text()
)
REVISIONS: list[dict[str, Any]] = VECTOR["revisions"]


def _codex() -> Callable[[int, list[Any]], list[Any]]:
    mapper = CodexEventMapper(thread_id="th_1", turn_id="trn_1")
    mapper.begin("cx_1")
    return lambda _n, items: mapper.feed(
        {"method": "turn/plan/updated", "params": {"threadId": "th_1", "turnId": "cx_1", "plan": items}}
    )


def _claude_code() -> Callable[[int, list[Any]], list[Any]]:
    mapper = ClaudeEventMapper(turn_id="trn_1")
    use = {"type": "tool_use", "name": "TodoWrite"}
    return lambda n, items: mapper.feed(
        {"type": "assistant", "message": {"id": f"m{n}", "content": [{**use, "id": f"tu_{n}", "input": {"todos": items}}]}}
    )


def _opencode() -> Callable[[int, list[Any]], list[Any]]:
    mapper = OpencodeEventMapper(session_id="ses_1", turn_id="trn_1")
    _ = mapper.feed({"type": "session.status", "properties": {"sessionID": "ses_1", "status": {"type": "busy"}}})

    def feed(n: int, items: list[Any]) -> list[Any]:
        part = {
            "type": "tool",
            "id": f"p{n}",
            "messageID": "msg_1",
            "callID": f"call_{n}",
            "tool": "todowrite",
            "state": {"status": "running", "input": {"todos": items}},
        }
        return mapper.feed({"type": "message.part.updated", "properties": {"sessionID": "ses_1", "part": part}})

    return feed


ENGINES = {"codex": _codex, "claude_code": _claude_code, "opencode": _opencode}


@pytest.mark.parametrize("engine", sorted(ENGINES))
def test_b1_7_each_engine_yields_the_vectors_events(engine: str) -> None:
    feed, ledger = ENGINES[engine](), TaskLedger()
    for n, revision in enumerate(REVISIONS, start=1):
        plans = [e for e in feed(n, revision["native"][engine]) if isinstance(e, PlanUpdated)]
        assert len(plans) == 1
        events = ledger.replace(plans[0].items)
        assert [{"type": e.type, "data": e.data} for e in events] == revision["events"], (engine, n)


async def test_b1_7_the_loop_yields_the_vectors_events() -> None:
    routes = PlanRoutes(TaskLedger(), lambda _names: True).routes()
    for n, revision in enumerate(REVISIONS, start=1):
        done = await routes["write_todos"](
            ToolCall(id=f"c{n}", name="write_todos", arguments={"todos": revision["native"]["loop"]})
        )
        assert [{"type": e.type, "data": e.data} for e in done.events] == revision["events"], n


def test_the_vector_covers_every_executor() -> None:
    assert all(set(r["native"]) == {"codex", "claude_code", "opencode", "loop"} for r in REVISIONS)

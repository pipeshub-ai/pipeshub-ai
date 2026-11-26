"""PF-05 (C7.04): what an engine's tool call waits on in the journal.

Each journal commit is a MongoDB transaction (~14 ms p50 on the reference host), so the commits a call waits on are
the tool gateway's overhead. A call's audit is its intent and its outcome (ARCHITECTURE §crash table, SLO #9's
2 per tool); the activity line showing its result is not audit, and rides the step's next batch (the engine's own
"completed" step event, which reaches the journal at once) instead of holding the engine's answer. The step's
"started" line is journaled from the engine's event stream, which races the call (it may commit first, under the
attempt's one commit order).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_core.testing.engines.durable import PROGRESS, EngineWorld

if TYPE_CHECKING:
    from harness_core.journal.types import CommitReceipt, CommitRequest
    from harness_core.testing.sandbox.egress import EgressRequest

RUN = "run-pf05"


def _kind(request: CommitRequest) -> str:
    parts = request.command_id.split(":")
    if len(parts) >= 3 and parts[-1].isdigit():
        return parts[-2]
    # The result's activity line must never hold the call; any other progress line (the step's "started" line, which
    # races the call from the engine's event stream) may commit on either side of the call's audit.
    shows_result = any(e.type == PROGRESS and e.data.get("kind") == "tool_result" for e in request.events)
    return "result_line" if shows_result else "progress"


async def test_pf05_c1_a_tool_call_answers_once_its_outcome_is_journaled() -> None:
    world = EngineWorld()
    during: list[list[str]] = []
    open_call: list[list[str]] = []
    commit = world.journal.commit
    handle = world.host.handle

    async def recording(request: CommitRequest) -> CommitReceipt:
        for commits in open_call:
            commits.append(_kind(request))
        return await commit(request)

    async def calling(request: EgressRequest) -> Any:  # noqa: ANN401
        if (request.body or {}).get("method") != "tools/call":  # type: ignore[union-attr]
            return await handle(request)
        commits: list[str] = []
        open_call.append(commits)
        try:
            return await handle(request)
        finally:
            open_call.remove(commits)
            during.append(commits)

    world.journal.commit = recording  # type: ignore[method-assign]
    world.proxy.route_upstream(world.host.base, calling)
    world.admit(RUN, 'tool lookup {"q": "a"}\ntool lookup {"q": "b"}\nsay done')
    assert (await world.attempt(RUN)).status == "completed"
    assert len(during) == 2
    for commits in during:
        # An earlier call's result line may ride the batch ahead of this call; this call's own comes after it answers.
        assert [c for c in commits if c not in ("progress", "result_line")] == ["intent", "outcome"], commits
        assert "result_line" not in commits[commits.index("intent") :], commits
    shown = [e.data for e in world.events(RUN) if e.type == PROGRESS and e.data["kind"] == "tool_result"]
    assert len(shown) == 2, "a result's activity line was not journaled"

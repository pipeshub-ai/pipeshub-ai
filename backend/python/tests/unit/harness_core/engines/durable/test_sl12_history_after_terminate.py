"""SL-12: a session's history after its run is terminated: the whole transcript reads back, the files it published
stay downloadable, and no sandbox is left.

One engine run on the in-memory world (``EngineWorld``: the journal, the broker, the artifact store a deployment keeps
across workers). Its first turn writes a table and answers; a follow-up turn is cancelled while its model call is
held. What a person opening the conversation afterwards gets is read from those stores alone.
"""

from __future__ import annotations

import asyncio

from harness_core.contracts.generated.common import ContentRef
from harness_core.interactions.ports import RunBinding
from harness_core.testing.engines.durable import ORG, SESSION, EngineWorld

RUN = "run-sl12"
# Larger than a placeholder, so the turn that wrote it settles it as its result (WP-70 keeps a small requested file a draft).
REPORT = "region,revenue " + " ".join(f"r{n},{n * 10}" for n in range(400))


async def test_sl12_after_terminate_the_transcript_reads_back_the_files_download_and_no_sandbox_is_left() -> None:
    world = EngineWorld()
    world.admit(RUN, f"write out/q3-table.csv {REPORT}\nhold\nsay The table is in q3-table.csv.")
    attempt = asyncio.ensure_future(world.attempt(RUN))
    await asyncio.wait_for(world.gateway.holding.wait(), 10)
    _ = world.journal.queue_input(RUN, "write out/draft.md never kept\nhold\nsay never")
    world.gateway.holding.clear()
    world.gateway.release()
    await asyncio.wait_for(world.gateway.holding.wait(), 10)
    assert world.types(RUN).count("turn.completed") == 1
    world.journal.request_cancel(RUN)
    world.wakeup.wake(RUN)
    disposition = await asyncio.wait_for(attempt, 10)
    world.gateway.release()
    assert disposition.status == "cancelled"
    assert world.journal.status(RUN) == "cancelled"

    # The transcript: the person's message and the one sent during the run, the first turn's reply, how it ended.
    events = world.events(RUN)
    types = [e.type for e in events]
    assert types.count("turn.started") == 2
    assert types[-1] == "run.cancelled"

    async def text(event_type: str, ref: str) -> list[str]:
        return [
            (await world.content.get(ContentRef.model_validate(e.data[ref]))).decode()
            for e in events
            if e.type == event_type
        ]

    [first] = await text("message.completed", "textRef")
    assert REPORT in first
    assert [t.splitlines()[0] for t in await text("input.queued", "textRef")] == ["write out/draft.md never kept"]
    [reply] = await text("model.response", "messageRef")
    assert "The table is in q3-table.csv." in reply

    # The first turn's file is the session's and downloads whole; the cancelled turn's draft was discarded.
    binding = RunBinding(ORG, SESSION, RUN)
    names = {a.name for a in world.artifacts.listing(binding)}
    assert names == {"q3-table.csv"}
    report = await world.artifacts.find_by_name(binding, "q3-table.csv")
    assert report is not None
    assert (await world.artifacts.content(binding, report.artifact_id, report.version)).decode() == REPORT

    # The sandbox is gone: every one the run acquired was released.
    assert world.broker.sandboxes == {}
    assert world.broker.acquisitions >= 1


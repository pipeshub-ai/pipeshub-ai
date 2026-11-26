"""P4.2 LD-11, LD-12: what the fold keeps for the compiler, and older checkpoints refold."""

from __future__ import annotations

from harness_core.journal.types import Checkpoint
from harness_core.kernel.fold import (
    checkpoint_is_valid,
    fold_run,
    fold_session,
    make_checkpoint,
    restore_checkpoint,
)
from tests.unit.harness_core.durable.support import RUN, SESSION, World, scripted

from .kernel_support import answered, compiler, looked_up

USAGES = [300, 300, 300, 6_000, 300, 8_500, 300]


async def _session() -> World:
    world = World()

    async def web(args, ctx):  # noqa: ANN202
        from harness_core.kernel.durable import ToolExecution

        return ToolExecution(status="ok", content={"p": 1}, integrity="external")

    world.tools.add("lookup", web, tags=[("category", "read")])
    world.admit(max_turns=30)
    _, port = scripted(*(looked_up(u, f"q{i}") for i, u in enumerate(USAGES)), answered())
    assert (await world.run(world.kernel(port, context=compiler()))).status == "completed"
    return world


async def test_ld12_the_fold_keeps_usage_labels_summaries_and_the_last_reduction() -> None:
    world = await _session()
    session = fold_session(world.journal.events(SESSION), stream_id=SESSION)
    responses = [e.response for e in session.entries if e.response is not None]
    assert [r.usage.input_tokens for r in responses if r.usage] == [*USAGES, 100]
    results = [r for e in session.entries for r in e.results.values()]
    assert all(r.labels == {"integrity": "external"} and r.seq is not None for r in results)
    [compaction] = session.compactions
    assert compaction.labels == {"integrity": "external"}
    assert session.compacted == [compaction.range]
    assert len(session.cleared) == 1
    assert session.reduced_at_seq == compaction.seq


async def test_ld11_a_checkpoint_without_the_derived_state_is_refolded() -> None:
    world = await _session()
    events = world.journal.events(SESSION)
    run = fold_run(events, run_id=RUN)
    session = fold_session(events, stream_id=SESSION)
    current = make_checkpoint(run, session)
    head = events[-1].seq
    assert checkpoint_is_valid(current, run_id=RUN, head_seq=head, ref="c", referenced_ref="c")
    assert restore_checkpoint(current)[1] == session
    older = dict(current.state["session"])  # type: ignore[arg-type]
    del older["compactions"], older["reduced_at_seq"]
    stale = Checkpoint(
        run_id=current.run_id,
        stream_id=current.stream_id,
        fold_version=current.fold_version,
        seq=current.seq,
        state={**current.state, "session": older},
    )
    assert not checkpoint_is_valid(stale, run_id=RUN, head_seq=head, ref="c", referenced_ref="c")

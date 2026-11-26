"""PERFA-11 (ENG-39; QA22-F1): an engine run's commits stay within the journal's event limit.

A save is presented with one line a file, so one save can make more events than a commit holds; the
sink sends them as consecutive batches. The in-memory journal refuses a larger commit as Node does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

import pytest

from harness_core.engines.durable.sink import SEQ_SPAN, JournalEngineEventSink
from harness_core.engines.events import command_id
from harness_core.interactions.ports import RunBinding
from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS
from harness_core.journal.types import NewEvent
from harness_core.testing.engines.durable import (
    PROGRESS,
    EngineWorld,
    RecordingPresenter,
)
from harness_core.workspace.types import build_manifest

if TYPE_CHECKING:
    from harness_core.engines.durable.ports import ProgressStamp
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.types import WorkspaceManifest

RUN = "run-wide"


@dataclass
class _LinePerFile(RecordingPresenter):
    """Shows a save as the product does: one line for each file of the revision."""

    lines: int | None = None

    @override
    async def saved(self, stamp: ProgressStamp, manifest: WorkspaceManifest, reason: CommitReason) -> list[NewEvent]:
        count = self.lines if self.lines is not None else len(manifest.files)
        return [self._line(stamp, "file", str(i)) for i in range(count)]


@pytest.mark.parametrize("lines", [MAX_COMMIT_EVENTS - 2, MAX_COMMIT_EVENTS - 1, MAX_COMMIT_EVENTS, 250])
async def test_perfa_11_a_save_shown_with_more_lines_than_a_commit_holds_is_sent_in_batches(lines: int) -> None:
    batches: list[tuple[str, list[NewEvent]]] = []

    async def commit(cid: str, events: list[NewEvent]) -> None:
        batches.append((cid, events))

    sink = JournalEngineEventSink("r", "r", _LinePerFile(lines=lines), commit)
    sink.begin("trn_x", 3)
    saved = build_manifest("ws:s", revision=1, parent_revision=None, files={}, created_at_ms=0)
    await sink.workspace_saved(RunBinding("o", "s", "r"), saved, "autosave")
    total = 1 + lines
    assert [len(events) for _, events in batches] == [MAX_COMMIT_EVENTS] * (total // MAX_COMMIT_EVENTS) + (
        [total % MAX_COMMIT_EVENTS] if total % MAX_COMMIT_EVENTS else []
    )
    journaled = [e for _, events in batches for e in events]
    assert journaled[0].type == "workspace.committed"
    assert [e.data["text"] for e in journaled[1:]] == [str(i) for i in range(lines)], "in order, none lost"
    ids = [cid for cid, _ in batches]
    assert len(set(ids)) == len(ids) and ids[0] == command_id("r", "trn_x", 3 * SEQ_SPAN)
    line_seqs = {int(str(e.data["seq"])) for e in journaled[1:]}
    assert all(cid not in {command_id("r", "trn_x", seq) for seq in line_seqs} for cid in ids[1:])


async def test_perfa_11_an_engine_run_whose_saves_outgrow_a_commit_completes() -> None:
    lines = MAX_COMMIT_EVENTS + 5
    world = EngineWorld()
    world.admit(RUN, "write a.md one\nwrite b.md two\nsay done")
    done = await world.attempt(RUN, kernel=world.kernel(presenter=_LinePerFile(lines=lines)))
    assert done.status == "completed"
    saves = [e for e in world.events(RUN) if e.type == "workspace.committed"]
    shown = [e for e in world.events(RUN) if e.type == PROGRESS and e.data["kind"] == "file"]
    assert saves and len(shown) == lines * len(saves), "every line of every save is journaled"
    assert world.duplicates() == {}


async def test_perfa_11_an_engine_commit_over_the_limit_is_consecutive_commits_with_the_transition_last() -> None:
    """What a turn publishes or recovers is one commit of unbounded size: cut like the loop kernel's."""
    from harness_core.engines.durable.kernel import (
        _EngineAttemptRun,  # pyright: ignore[reportPrivateUsage]
    )
    from harness_core.journal.types import Fail

    world = EngineWorld()
    world.admit(RUN, "say hi")
    claim = await world.journal.claim(RUN, worker_id="w1")
    assert claim is not None
    kernel = world.kernel()
    run = _EngineAttemptRun(kernel, claim, kernel.clock.suspend_aware())
    events = [NewEvent(PROGRESS, {"n": n}, critical=False) for n in range(2 * MAX_COMMIT_EVENTS + 30)]
    _ = await run.commit("publication", events, Fail("engine_failed"))
    commits = [types for command, types in world.journal.commits(RUN) if ":publication:" in command]
    assert [len(types) for types in commits] == [MAX_COMMIT_EVENTS, MAX_COMMIT_EVENTS, 31]
    assert commits[-1][-1] == "run.failed" and "run.failed" not in commits[0] + commits[1]
    assert [e.data["n"] for e in world.events(RUN) if e.type == PROGRESS] == list(range(len(events)))

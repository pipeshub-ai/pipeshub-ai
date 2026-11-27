"""EV2-22 / E1b PX-*: a save committed by an attempt that then dies is published exactly once.

Found by the wave 2 crash slice (EV2-F2): a worker killed between a workspace save and its publication
(``engine.turn.after_autosave``, ``engine.turn.after_save_turn_end``) left the edited file in the workspace and
not in the artifacts. Fixed in E1b: a save logs its outputs' publication intents before the store commit, and
an attempt that re-drives the turn publishes every intent the head holds and no attempt published.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import afailpoint
from harness_core.interactions.ports import RunBinding
from harness_core.journal.types import TERMINAL_STATUSES, NewEvent, OwnershipLost
from harness_core.kernel.durable.lease import LeaseLost
from harness_core.testing.engines.durable import ORG, PROGRESS, SESSION, EngineWorld
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from harness_core.workspace.changes import ChangeSet, CommitReason
    from harness_core.workspace.types import WorkspaceManifest

RUN = "run-unpublished"
# Two changes per autosave: the first autosave holds out/report.md, the turn's end has nothing new to save.
PROMPT = "write out/report.md edited\nwrite notes/a.md a\nsay done"
WORKSPACE = f"ws:{SESSION}"


def world() -> EngineWorld:
    w = EngineWorld()
    w.config = dataclasses.replace(w.config, autosave_changes=2)
    return w


async def finish(w: EngineWorld, run_id: str) -> str:
    for _ in range(6):
        if w.journal.status(run_id) in TERMINAL_STATUSES:
            break
        if w.journal.status(run_id) == "running":
            w.expire()
        _ = await w.attempt(run_id)
    return str(w.journal.status(run_id))


async def published(w: EngineWorld) -> dict[str, list[bytes]]:
    binding = RunBinding(ORG, SESSION, "check")
    out: dict[str, list[bytes]] = {}
    for stored in w.artifacts.listing(binding):
        out[stored.name] = [await w.artifacts.content(binding, stored.artifact_id, v) for v in range(1, stored.version + 1)]
    return out


async def history_counts(w: EngineWorld) -> tuple[int, int]:
    history = await w.publications.history(RunBinding(ORG, SESSION, RUN))
    return len(history.intents), len(history.published)


@pytest.mark.parametrize("point", ["engine.turn.after_autosave", "engine.turn.after_save_turn_end"])
async def test_px3_px4_a_save_committed_by_a_dead_attempt_is_published_on_resume(point: str) -> None:
    w = world()
    prompt = PROMPT if point == "engine.turn.after_autosave" else "write out/report.md edited\nsay done"
    w.admit(RUN, prompt)
    assert await w.crash_at(RUN, point)
    assert await finish(w, RUN) == "completed"
    assert await published(w) == {"report.md": [b"edited"]}
    assert await history_counts(w) == (1, 1)


async def test_px1_the_intent_is_logged_before_the_revision_exists() -> None:
    w = world()
    seen: list[int] = []
    commit = w.store.commit

    async def recording(
        scope: ObjectScope, workspace_id: str, *, base_revision: int, changes: ChangeSet, reason: CommitReason
    ) -> WorkspaceManifest:
        if "out/report.md" in changes.upserts:
            seen.append(len((await w.publications.history(RunBinding(ORG, SESSION, RUN))).intents))
        return await commit(scope, workspace_id, base_revision=base_revision, changes=changes, reason=reason)

    w.store.commit = recording  # type: ignore[method-assign]
    w.admit(RUN, PROMPT)
    assert await finish(w, RUN) == "completed"
    assert seen == [1]
    assert await history_counts(w) == (1, 1)
    assert await published(w) == {"report.md": [b"edited"]}


async def test_px5_a_crash_after_the_intent_before_the_commit_publishes_the_redo_once() -> None:
    w = world()
    w.admit(RUN, PROMPT)
    assert await w.crash_at(RUN, "engine.turn.after_prepare")
    head = await w.store.head(ObjectScope(ORG), WORKSPACE)
    assert "out/report.md" not in head.files
    assert await finish(w, RUN) == "completed"
    assert await published(w) == {"report.md": [b"edited"]}
    assert await history_counts(w) == (1, 1)


async def test_px6_the_recovered_save_is_journaled_once_with_its_paths() -> None:
    w = world()
    w.admit(RUN, PROMPT)
    assert await w.crash_at(RUN, "engine.turn.after_autosave")
    saved = (await w.store.head(ObjectScope(ORG), WORKSPACE)).revision
    assert [e for e in w.events(RUN) if e.type == "workspace.committed"] == []
    assert await finish(w, RUN) == "completed"
    commits = [e.data for e in w.events(RUN) if e.type == "workspace.committed"]
    assert [c["revision"] for c in commits].count(saved) == 1
    recovered = next(c for c in commits if c["revision"] == saved)
    assert recovered["changed"] == ["/notes/a.md", "/out/report.md"]


async def test_px6_a_save_journaled_but_not_published_is_published_and_not_journaled_again() -> None:
    w = world()
    register = w.artifacts.register_output

    async def killed_before_the_registry(binding: RunBinding, **kw: object) -> object:
        await afailpoint("test.before_register")
        return await register(binding, **kw)  # type: ignore[arg-type]

    w.artifacts.register_output = killed_before_the_registry  # type: ignore[method-assign]
    w.admit(RUN, PROMPT)
    assert await w.crash_at(RUN, "test.before_register")
    assert [e.data["changed"] for e in w.events(RUN) if e.type == "workspace.committed"] == [["/notes/a.md", "/out/report.md"]]
    assert await finish(w, RUN) == "completed"
    commits = [e.data["revision"] for e in w.events(RUN) if e.type == "workspace.committed"]
    assert len(commits) == len(set(commits))
    assert await published(w) == {"report.md": [b"edited"]}
    assert await history_counts(w) == (1, 1)


async def test_px8_a_superseded_attempt_cannot_log_a_publication() -> None:
    w = world()
    w.admit(RUN, PROMPT)
    first = await w.engine_attempt(RUN)
    w.expire()
    second = await w.engine_attempt(RUN)
    line = NewEvent(PROGRESS, {"taskId": RUN, "turnId": "t", "seq": 1, "kind": "x", "text": "", "data": {}}, critical=False)
    with pytest.raises((LeaseLost, OwnershipLost)):
        await first.append([line])
    await second.append([line])


async def test_ev2_22_a_crash_after_the_publication_publishes_once() -> None:
    w = world()
    w.admit(RUN, PROMPT)
    assert await w.crash_at(RUN, "engine.turn.after_publish")
    assert await finish(w, RUN) == "completed"
    assert await published(w) == {"report.md": [b"edited"]}
    assert await history_counts(w) == (1, 1)

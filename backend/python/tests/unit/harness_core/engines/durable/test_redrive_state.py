"""E1b RD-*: the state a re-driven turn starts from (EV2-24).

A turn re-driven after its attempt died starts from the workspace that attempt saved (its saves are facts, as
its tool effects are), and its prompt names the files saved during the turn, so the engine reads them before
changing them instead of applying an edit a second time.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, override

from harness_core.engines.durable.kernel import INTERRUPTED_NOTE, SAVED_NOTE
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.testing.engines.durable import EngineWorld, ScriptedEngineAdapter
from harness_core.testing.sandbox.fake import FakeSandboxHandle

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.events import EngineState
    from harness_core.engines.ports import EngineSession
    from harness_core.engines.types import EngineLaunch
    from harness_core.sandbox.ports import SandboxHandle

RUN = "run-redrive"


class _Opening(ScriptedEngineAdapter):
    """Records the workspace files each session finds in its sandbox when it opens (before the engine runs)."""

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[list[str]] = []

    @override
    async def open(  # pyright: ignore[reportIncompatibleMethodOverride] - the scripted adapter's open is untyped
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> EngineSession:
        assert isinstance(sandbox, FakeSandboxHandle)
        root = launch.workdir.rstrip("/") + "/"
        self.opened.append(sorted(p.removeprefix(root) for p in sandbox.files if p.startswith(root)))
        return await super().open(sandbox, launch, restore, correlation=correlation)  # pyright: ignore[reportReturnType]


def world(adapter: ScriptedEngineAdapter | None = None) -> EngineWorld:
    w = EngineWorld(adapter=adapter) if adapter is not None else EngineWorld()
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


def last_prompt(w: EngineWorld) -> str:
    return [m.content for m in w.gateway.model_calls[-1].messages if m.role == "user"][-1]


async def test_rd1_the_redo_starts_from_the_saved_head_and_is_told_what_was_saved() -> None:
    adapter = _Opening()
    w = world(adapter)
    w.admit(RUN, "write out/report.md edited\nwrite notes/a.md a\nsay done")
    assert await w.crash_at(RUN, "engine.turn.after_autosave")
    assert await finish(w, RUN) == "completed"
    # Not the pre-turn workspace (empty): the head the dead attempt saved.
    assert adapter.opened == [[], ["notes/a.md", "out/report.md"]]
    prompt = last_prompt(w)
    assert prompt.startswith(INTERRUPTED_NOTE)
    assert f"{SAVED_NOTE}notes/a.md, out/report.md." in prompt


async def test_rd2_a_redo_with_nothing_saved_gets_the_note_alone() -> None:
    w = world()
    w.admit(RUN, "write out/report.md edited\nsay done")
    assert await w.crash_at(RUN, "engine.turn.after_restore")
    assert await finish(w, RUN) == "completed"
    prompt = last_prompt(w)
    assert prompt.startswith(INTERRUPTED_NOTE)
    assert SAVED_NOTE not in prompt


async def test_rd3_files_saved_by_an_earlier_turn_are_not_this_turns() -> None:
    w = world()
    w.admit(RUN, "write early.md e\nwrite early2.md f\nsay first")
    _ = w.journal.queue_input(RUN, "write out/second.md two\nwrite notes/b.md b\nsay second")
    assert await w.crash_at(RUN, "engine.turn.after_autosave", nth=2)
    assert await finish(w, RUN) == "completed"
    prompt = last_prompt(w)
    assert f"{SAVED_NOTE}notes/b.md, out/second.md." in prompt
    assert "early" not in prompt.split(SAVED_NOTE, 1)[1]

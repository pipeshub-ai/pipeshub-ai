"""RUN01-D1-KERNEL: an engine run's end branches record what it produced in its ledger and ask the presenter for the
words; nothing re-reads the request, a refused save is a fact the words follow, and a file the engine wrote is never
replaced (ADR-RUN-01 D1, the D6 save row)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from harness_core.artifacts.types import Derivation
from harness_core.effects.types import ToolExecution
from harness_core.engines.durable.handoff import HANDOFF_PATH, save_handoff
from harness_core.engines.outcome.deliverable import Deliverable
from harness_core.engines.outcome.ledger import (
    OUTCOME_RECORDED,
    OUTCOME_SETTLED,
    PLACEHOLDER_MAX_BYTES,
)
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.interactions.ports import RunBinding
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.forks import session_scope
from harness_core.workspace.types import InvalidChangeSet, ObjectScope, WorkspaceFile

if TYPE_CHECKING:
    import pytest

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.effects.types import ToolContext
    from harness_core.kernel.core.messages import ToolCall

RUN = "run-run01-d1"
REQUEST = "Research how Onyx compares with PipesHub. Write the final report as one Markdown file named report.md in out/"


async def _fetch(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    del ctx
    url = str(call.arguments.get("url"))
    return ToolExecution(status="ok", content=json.dumps({"ok": True, "url": url, "title": "Onyx", "blocks": []}))


async def _broken(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    del call, ctx
    return ToolExecution(status="error", content="upstream 502")


def _lookups(count: int) -> list[str]:
    return [f'tool lookup {json.dumps({"url": f"https://docs.onyx.app/p{n}"})}' for n in range(count)]


async def _run(world: EngineWorld, script: list[str], *, max_steps: int = 40, spec: RunSpec | None = None) -> str:
    world.admit(RUN, "\n".join([REQUEST, *script]), spec=spec or engine_run_spec(engine_profile(max_steps=max_steps)))
    assert (await world.attempt(RUN)).status == "completed"
    return await world.plain_answer(RUN)


def _settled(world: EngineWorld) -> dict[str, Any]:
    (event,) = [e for e in world.events(RUN) if e.type == OUTCOME_SETTLED]
    return event.data


def _facts(world: EngineWorld) -> list[dict[str, Any]]:
    return [f for e in world.events(RUN) if e.type == OUTCOME_RECORDED for f in e.data["facts"]]  # type: ignore[union-attr]


async def _handoff(world: EngineWorld) -> bool:
    return HANDOFF_PATH in (await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")).files


async def test_run01_d1_kernel_1_the_pinned_deliverable_is_read_and_the_request_is_not_parsed_again() -> None:
    world = EngineWorld(adapter=ScriptedEngineAdapter(), handlers={}, wrap_up=None)
    spec = engine_run_spec(engine_profile(max_steps=40))
    pinned = spec.model_copy(update={"extensions": {"x.pipeshub": {"deliverable": {"kind": "answer"}}}})
    answer = await _run(world, ["say Onyx documents its connectors."], spec=pinned)
    assert answer == "Onyx documents its connectors.", "admission pinned an answer: no file is owed, whatever the words"
    settled = _settled(world)
    assert settled["deliverable"] == {"kind": "answer"} and settled["outcome"]["kind"] == "completed"


async def test_run01_d1_kernel_2_a_cut_that_gathered_nothing_offers_retry_and_saves_nothing() -> None:
    world = EngineWorld(adapter=ScriptedEngineAdapter(), handlers={"lookup": _broken}, wrap_up=None)
    answer = await _run(world, [*_lookups(12), "say done"], max_steps=6)
    assert answer.startswith("**report.md isn't written yet.**") and "before it started on the work" in answer
    assert "saved" not in answer and answer.endswith('Send "continue" to try the task again.')
    assert not await _handoff(world), "there is nothing to continue from"
    outcome = _settled(world)["outcome"]
    assert (outcome["kind"], outcome["reason"], outcome["action"]) == ("partial", "turns", "retry")
    assert outcome["missing"] == ["report.md"] and outcome["delivered"]["sources"] == 0
    assert {"kind": "limit_reached", "limit": "turns"} in _facts(world)


async def test_run01_d1_kernel_3_gathered_sources_are_journaled_as_facts_and_the_words_say_they_are_kept() -> None:
    world = EngineWorld(adapter=ScriptedEngineAdapter(), handlers={"lookup": _fetch}, wrap_up=None)
    answer = await _run(world, [*_lookups(12), "say done"], max_steps=6)
    assert f"({HANDOFF_PATH} lists it)" in answer and await _handoff(world)
    facts = _facts(world)
    sources = [f for f in facts if f["kind"] == "source_gathered"]
    assert sources and all(f["url"].startswith("https://docs.onyx.app/p") for f in sources)
    assert {"kind": "note_saved", "path": HANDOFF_PATH} in facts
    assert _settled(world)["outcome"]["action"] == "continue"


async def test_run01_d1_kernel_4_a_refused_save_is_a_fact_and_the_answer_never_claims_the_work_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = EngineWorld(adapter=ScriptedEngineAdapter(), handlers={"lookup": _fetch}, wrap_up=None)
    commit = world.store.commit

    async def refusing(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - the store's own signature
        changes: ChangeSet = kwargs["changes"]
        if HANDOFF_PATH in changes.paths:
            raise InvalidChangeSet("WORKSPACE_TOO_LARGE")
        return await commit(*args, **kwargs)

    monkeypatch.setattr(world.store, "commit", refusing)
    answer = await _run(world, [*_lookups(12), "say done"], max_steps=6)
    assert "could not be saved" in answer and HANDOFF_PATH not in answer
    assert answer.endswith('Send "continue" to try the task again.')
    assert {"kind": "save_refused", "reason": "WORKSPACE_TOO_LARGE"} in _facts(world)
    outcome = _settled(world)["outcome"]
    assert outcome["delivered"]["kept"] is False and outcome["action"] == "retry"


async def test_run01_d1_kernel_5_a_file_the_engine_wrote_is_never_replaced_by_a_handoff() -> None:
    world = EngineWorld(adapter=ScriptedEngineAdapter(), handlers={}, wrap_up=None)
    scope, blob, workspace = ObjectScope(ORG), session_scope(ORG, SESSION), f"ws:{SESSION}"
    report = "# Report\n\n" + "Finding. " * PLACEHOLDER_MAX_BYTES
    entry = WorkspaceFile("out/report.md", await world.objects.put(blob, report.encode()), "text/markdown")
    base = await world.store.head(scope, workspace)
    _ = await world.store.commit(scope, workspace, base_revision=base.revision, changes=ChangeSet.of(entry), reason="turn_end")
    saved = await save_handoff(
        store=world.store, objects=world.objects, publisher=None,  # type: ignore[arg-type] - nothing is published
        binding=RunBinding(ORG, SESSION, RUN), object_scope=blob, workspace_id=workspace, request=REQUEST,
        answer="A rewrite from scraps.", reason="time", calls=[], wrote_outputs=False,
        derivation=Derivation(task_id=RUN, source_records=None), deliverable=Deliverable("file", "report.md"),
        banner=PRESENTER.banner("time", synthesized=True),
    )
    assert saved.deliverable is None and saved.publication is None
    kept = saved.manifest.files["out/report.md"]
    assert (await world.objects.get(blob, kept.content)).decode() == report, "the engine's report stands"

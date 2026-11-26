"""WP70-DONE: an engine that ends its turn normally ("done") with the requested file still a placeholder.

DEEPFINISH's placeholder failure without a limit: the turn completes, the controller finalized every draft, and the
178-byte placeholder stood as the deliverable. The run now writes the file from what was gathered (the wrap-up, on its
reserve), publishes it final over the placeholder, and says so; without a wrap-up answer the placeholder stays a draft
and the answer says the file is not written yet.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness_core.effects.types import ToolExecution
from harness_core.interactions.ports import RunBinding
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)
from harness_core.testing.engines.scripted_wrap_up import ScriptedWrapUp
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from harness_core.effects.types import ToolContext
    from harness_core.kernel.core.messages import ToolCall

RUN = "run-wp70-done"
REQUEST = "Research Onyx's connectors. Cite every finding with its URL. Write the report as report.md in your output folder."


async def _fetch(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    del ctx
    url = str(call.arguments.get("url"))
    page = {"ok": True, "url": url, "title": "Onyx docs", "blocks": [{"type": "text", "content": f"Fact at {url}."}]}
    return ToolExecution(status="ok", content=json.dumps(page))


def _script() -> str:
    reads = [f'tool lookup {json.dumps({"url": f"https://docs.onyx.app/page-{n}"})}' for n in range(3)]
    return "\n".join([REQUEST, "write out/report.md Research in progress.", *reads, "say done"])


def _world(wrap_up: ScriptedWrapUp | None) -> EngineWorld:
    return EngineWorld(
        adapter=ScriptedEngineAdapter(), handlers={"lookup": _fetch},
        wrap_up=(lambda claim: wrap_up) if wrap_up is not None else None,
    )


async def _run(world: EngineWorld) -> str:
    world.admit(RUN, _script(), spec=engine_run_spec(engine_profile(max_steps=40)))
    assert (await world.attempt(RUN)).status == "completed"
    return await world.plain_answer(RUN)


async def _report(world: EngineWorld) -> tuple[str, int, str]:
    binding = RunBinding(ORG, SESSION, RUN)
    stored = await world.artifacts.find_by_name(binding, "report.md")
    assert stored is not None
    content = await world.artifacts.content(binding, stored.artifact_id, stored.version)
    return stored.lifecycle, stored.version, content.decode("utf-8")


async def test_wp70_done_a_placeholder_at_a_normal_end_becomes_the_cited_report() -> None:
    wrap_up = ScriptedWrapUp()
    world = _world(wrap_up)
    answer = await _run(world)
    completed = [e.data for e in world.events(RUN) if e.type == "run.completed"]
    assert completed == [{"outcome": "answered"}], "a normal end, not a limit"
    lifecycle, version, report = await _report(world)
    assert lifecycle == "final" and version >= 2 and "Research in progress" not in report
    assert "[source](https://docs.onyx.app/page-0)" in report
    assert len(wrap_up.prompts) == 1 and "Fact at https://docs.onyx.app/page-2." in wrap_up.prompts[0]
    assert "**report.md**" in answer and "[source](https://docs.onyx.app/page-0)" in answer
    assert answer.strip() != "done"
    head = await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")
    assert "notes/handoff.json" in head.files, "continue can extend it"


async def test_wp70_done_without_a_wrap_up_the_placeholder_stays_a_draft_and_the_answer_says_so() -> None:
    world = _world(None)
    answer = await _run(world)
    lifecycle, version, report = await _report(world)
    assert (lifecycle, version) == ("draft", 1) and "Research in progress" in report
    assert answer.startswith("**report.md isn't written yet.**") and "https://docs.onyx.app/page-0" in answer
    assert '"continue"' in answer


async def test_wp70_done_a_real_report_at_a_normal_end_is_left_as_the_engine_wrote_it() -> None:
    wrap_up = ScriptedWrapUp()
    world = _world(wrap_up)
    body = "Onyx " * 400
    world.admit(RUN, "\n".join([REQUEST, f"write out/report.md {body}", "say done"]), spec=engine_run_spec(engine_profile(max_steps=40)))
    assert (await world.attempt(RUN)).status == "completed"
    assert wrap_up.prompts == []
    lifecycle, _, report = await _report(world)
    assert lifecycle == "final" and report.startswith("Onyx")


def test_wp70_done_what_a_normal_end_still_owes() -> None:
    from harness_core.engines.outcome.deliverable import resolve_deliverable
    from harness_core.engines.outcome.ledger import (
        PLACEHOLDER_MAX_BYTES,
        OutcomeLedger,
        is_placeholder_text,
        workspace_artifacts,
    )
    from harness_core.workspace.types import ObjectRef, WorkspaceFile

    def owed_at_end(request: str, files: dict[str, WorkspaceFile]) -> str | None:
        deliverable = resolve_deliverable(request)
        return OutcomeLedger(workspace_artifacts(files, 1, deliverable)).owed_at_end(deliverable)

    def file(path: str, size: int) -> WorkspaceFile:
        return WorkspaceFile(path, ObjectRef(f"o-{path}", "h", size))

    # Only a file the request names: "a file" answered in the chat, or at the person's path, is the engine's call.
    assert owed_at_end("Make a file of the open bugs.", {}) is None
    assert owed_at_end(REQUEST, {}) == "report.md"
    assert owed_at_end("Make location-summary.csv from it.", {}) == "location-summary.csv"
    assert owed_at_end(REQUEST, {"out/x.md": file("out/x.md", PLACEHOLDER_MAX_BYTES)}) is None
    assert owed_at_end(REQUEST, {"docs/report.md": file("docs/report.md", PLACEHOLDER_MAX_BYTES)}) is None
    # A small file is owed by size; the kernel then reads it: a short real file stands, a placeholder does not.
    assert is_placeholder_text("Research in progress. Final report will follow.")
    assert is_placeholder_text("  ") and is_placeholder_text("TODO")
    assert not is_placeholder_text("Location,Headcount\nLisbon,14\n") and not is_placeholder_text("quarterly")

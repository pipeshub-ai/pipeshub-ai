"""WP-70 DEEPFINISH: a cut-short engine run still ends with its deliverable (WP70-OWED, WP70-CUT, WP70-NOTES).

A deep research run: Codex wrote a 178-byte "Research in progress" out/report.md first, gathered 166 pages, and
the step limit cut its turn. Its last message was a progress remark, so the wrap-up never ran (it ran only on an empty
text), and the placeholder was finalized as the deliverable. Here the engine does the same on a 16-step allowance.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness_core.effects.types import ToolExecution
from harness_core.engines.durable.wrap_up import ENGINE_DRAFT, ENGINE_NOTES
from harness_core.engines.outcome.deliverable import resolve_deliverable
from harness_core.engines.outcome.ledger import (
    PLACEHOLDER_MAX_BYTES,
    OutcomeLedger,
    workspace_artifacts,
)
from harness_core.interactions.ports import RunBinding
from harness_core.kernel.core.turn_limit import is_cut_short_fallback
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)
from harness_core.testing.engines.scripted_wrap_up import ScriptedWrapUp
from harness_core.workspace.types import ObjectRef, ObjectScope, WorkspaceFile

if TYPE_CHECKING:
    from harness_core.effects.types import ToolContext
    from harness_core.kernel.core.messages import ToolCall

RUN = "run-wp70-deep"
PAGES = 8
PLACEHOLDER = "# Onyx vs PipesHub\\n\\nResearch in progress. Final report will follow."
REQUEST = (
    "Do a deep research comparing Onyx and PipesHub. Cite every finding with its source URL. "
    "Write the final report as one Markdown file named report.md in your output folder."
)


def _page(url: str) -> str:
    n = url.rsplit("-", 1)[-1]
    return json.dumps({
        "ok": True, "result_type": "url_content", "url": url, "title": f"Onyx docs {n}",
        "blocks": [{"type": "text", "content": f"Onyx feature {n}: connector number {n} with permission sync."}],
    })


async def _fetch(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    del ctx
    return ToolExecution(status="ok", content=_page(str(call.arguments.get("url"))))


def _script() -> str:
    reads = [f'tool lookup {json.dumps({"url": f"https://docs.onyx.app/page-{i}"})}' for i in range(PAGES)]
    more = [f'tool lookup {json.dumps({"url": f"https://docs.onyx.app/page-{i}"})}' for i in range(PAGES, PAGES + 20)]
    return "\n".join([
        f"write out/report.md {PLACEHOLDER}",
        *reads[:4],
        "write notes/progress.md Onyx lists its connectors; PipesHub documents permission-aware indexing.",
        *reads[4:],
        *more,
        "say I'm focusing on demonstrated differences rather than marketing claims.",
    ])


def _world(wrap_up: ScriptedWrapUp | None) -> EngineWorld:
    return EngineWorld(
        adapter=ScriptedEngineAdapter(),
        handlers={"lookup": _fetch},
        wrap_up=(lambda claim: wrap_up) if wrap_up is not None else None,
    )


async def _answer(world: EngineWorld) -> str:
    return await world.plain_answer(RUN)


async def _file(world: EngineWorld, path: str) -> str | None:
    head = await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")
    entry = head.files.get(path)
    if entry is None:
        return None
    return (await world.objects.get(ObjectScope(ORG, f"ws:{SESSION}"), entry.content)).decode("utf-8")


async def _report_artifact(world: EngineWorld) -> tuple[str, int, bytes]:
    binding = RunBinding(ORG, SESSION, RUN)
    stored = await world.artifacts.find_by_name(binding, "report.md")
    assert stored is not None
    return stored.lifecycle, stored.version, await world.artifacts.content(binding, stored.artifact_id, stored.version)


async def _run(world: EngineWorld) -> None:
    world.admit(RUN, f"{REQUEST}\n{_script()}", spec=engine_run_spec(engine_profile(max_steps=16)))
    assert (await world.attempt(RUN)).status == "completed"
    completed = [e.data for e in world.events(RUN) if e.type == "run.completed"]
    assert completed == [{"outcome": "answered", "truncatedBy": "turns"}]


async def test_wp70_owed_a_placeholder_report_becomes_the_wrap_ups_cited_report_marked_final() -> None:
    wrap_up = ScriptedWrapUp()
    world = _world(wrap_up)
    await _run(world)
    report = await _file(world, "out/report.md")
    assert report is not None and "Research in progress" not in report
    assert report.startswith("> **Partial**: written from the sources gathered")
    assert "[source](https://docs.onyx.app/page-0)" in report and "## Still to do" in report
    lifecycle, version, content = await _report_artifact(world)
    assert lifecycle == "final" and version >= 2 and content.decode("utf-8") == report
    answer = await _answer(world)
    assert "[source](https://docs.onyx.app/page-0)" in answer and "**report.md**" in answer
    assert "focusing on demonstrated differences" not in answer.split("---")[0]
    assert not is_cut_short_fallback(answer)


async def test_wp70_notes_the_wrap_up_reads_the_engines_notes_and_each_results_source() -> None:
    wrap_up = ScriptedWrapUp()
    await _run(_world(wrap_up))
    (gathered,) = wrap_up.gathered
    notes = [g for g in gathered if g.tool == ENGINE_NOTES]
    assert [(n.source, n.text) for n in notes] == [
        ("notes/progress.md", "Onyx lists its connectors; PipesHub documents permission-aware indexing.")
    ]
    # The engine's own step bound ended it mid-step, so it left no last message (the draft case: ENGBUDGET's tests).
    assert ENGINE_DRAFT not in {g.tool for g in gathered}
    sources = [g.source for g in gathered if g.tool == "lookup"]
    assert sources and all(s is not None and s.startswith("https://docs.onyx.app/page-") for s in sources)
    (prompt,) = wrap_up.prompts
    assert "Notes the agent kept" in prompt and "Onyx feature 0: connector number 0" in prompt
    assert '"blocks"' not in prompt, "a fetched page reaches the writer as its text, not its JSON frame"


async def test_wp70_owed_without_a_wrap_up_the_placeholder_stays_a_draft_and_the_answer_says_it_is_not_written() -> None:
    world = _world(None)
    await _run(world)
    lifecycle, version, content = await _report_artifact(world)
    assert lifecycle == "draft" and b"Research in progress" in content and version == 1
    answer = await _answer(world)
    assert answer.startswith("**report.md isn't written yet.**") and "step limit" in answer
    assert "https://docs.onyx.app/page-0" in answer and '"continue"' in answer
    assert "focusing on demonstrated differences" not in answer
    assert is_cut_short_fallback(answer), "a parent recognises it and writes the report itself"


def _file_of(path: str, size: int) -> WorkspaceFile:
    return WorkspaceFile(path, ObjectRef(f"o-{path}", "h", size), "text/markdown")


def unfinished_deliverable(request: str, files: dict[str, WorkspaceFile]) -> str | None:
    """What a cut-short run still owes, read from its ledger against the request's deliverable (ADR-RUN-01 D1)."""
    deliverable = resolve_deliverable(request)
    return OutcomeLedger(workspace_artifacts(files, 1, deliverable)).owed_after_cut(deliverable)


def test_wp70_owed_what_counts_as_the_deliverable_still_owed() -> None:
    small = PLACEHOLDER_MAX_BYTES - 1
    big = PLACEHOLDER_MAX_BYTES
    # Asked for, written only as a placeholder, or not at all: owed.
    assert unfinished_deliverable(REQUEST, {"out/report.md": _file_of("out/report.md", small)}) == "report.md"
    assert unfinished_deliverable(REQUEST, {"notes/a.md": _file_of("notes/a.md", 50_000)}) == "report.md"
    # Any real file under out/ is the engine's deliverable, whatever it named it.
    assert unfinished_deliverable(REQUEST, {"out/onyx.md": _file_of("out/onyx.md", big)}) is None
    # A request for no file owes none.
    assert unfinished_deliverable("Which of these are security bugs?", {}) is None

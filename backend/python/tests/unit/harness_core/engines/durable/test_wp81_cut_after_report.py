"""WP-81: a limit that cuts an engine after it wrote the requested file delivers that file (WP81-CUT).

A research run: Codex wrote a 143 KB cited out/report.md (seven published versions) and was
still editing it when its 30-minute working time ran out. The cut-short path then asked the wrap-up to write the
answer anyway: one more model call (2.7 minutes, USD 0.13, 8,000 output tokens) rewrote the request from tool-result
scraps, and that 26 KB rewrite, with 11 citations, became the chat's answer. Here the engine does the same on a
16-step allowance.
"""

from __future__ import annotations

import json

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

RUN = "run-wp81-cut"
REQUEST = (
    "Do a deep research comparing Onyx and PipesHub. Cite every finding with its source URL. "
    "Write the final report as one Markdown file named report.md in your output folder."
)
SECTIONS = 40


def _report() -> str:
    """A real report: a title, an executive summary, and cited sections past the chat's summary length."""
    sections = [
        f"## {n}. Finding {n}\\n\\nOnyx documents connector {n} with permission sync "
        f"([docs {n}](https://docs.onyx.app/page-{n})). " + "Detail of the finding. " * 12
        for n in range(1, SECTIONS + 1)
    ]
    return "\\n\\n".join([
        "# Onyx vs PipesHub",
        "## Executive summary\\n\\nOnyx documents connector validation ([changelog](https://docs.onyx.app/changelog)).",
        *sections,
    ])


def _script() -> str:
    lookups = [f'tool lookup {json.dumps({"url": f"https://docs.onyx.app/page-{i}"})}' for i in range(24)]
    return "\n".join([*lookups[:4], f"write out/report.md {_report()}", *lookups[4:], "say still editing the report"])


def _world(wrap_up: ScriptedWrapUp | None) -> EngineWorld:
    return EngineWorld(adapter=ScriptedEngineAdapter(), wrap_up=(lambda claim: wrap_up) if wrap_up is not None else None)


async def _answer(world: EngineWorld) -> str:
    return await world.plain_answer(RUN)


async def _run(world: EngineWorld) -> None:
    world.admit(RUN, f"{REQUEST}\n{_script()}", spec=engine_run_spec(engine_profile(max_steps=16)))
    assert (await world.attempt(RUN)).status == "completed"
    completed = [e.data for e in world.events(RUN) if e.type == "run.completed"]
    assert completed == [{"outcome": "answered", "truncatedBy": "turns"}]


async def test_wp81_cut_1_the_engines_written_report_is_the_result_and_no_wrap_up_is_asked() -> None:
    wrap_up = ScriptedWrapUp()
    world = _world(wrap_up)
    await _run(world)
    assert wrap_up.prompts == [], "the report the engine wrote is not rewritten from tool-result scraps"
    binding = RunBinding(ORG, SESSION, RUN)
    stored = await world.artifacts.find_by_name(binding, "report.md")
    assert stored is not None and stored.lifecycle == "final"
    content = (await world.artifacts.content(binding, stored.artifact_id, stored.version)).decode("utf-8")
    assert content.startswith("# Onyx vs PipesHub") and "> **Partial**" not in content
    assert f"https://docs.onyx.app/page-{SECTIONS}" in content
    answer = await _answer(world)
    # The chat gets the report's opening and its name, flagged partial, never a rewrite or the no-model notice (the
    # scripted engine writes the report on one line, so its opening is cut at the summary length, not a section).
    assert answer.startswith("# Onyx vs PipesHub") and len(answer) < len(content)
    assert "[changelog](https://docs.onyx.app/changelog)" in answer
    assert "The full report is in **report.md**." in answer
    assert "**This result is partial.**" in answer and "**report.md** holds what the task had written" in answer
    assert "still editing the report" not in answer and not is_cut_short_fallback(answer)
    # No wrap-up call: the run journals no usage beyond its turn's.
    assert [e for e in world.events(RUN) if e.type == "usage.recorded"] == []


async def test_wp81_cut_2_the_handoff_keeps_the_written_report_for_a_continue() -> None:
    world = _world(ScriptedWrapUp())
    await _run(world)
    head = await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")
    scope = ObjectScope(ORG, f"ws:{SESSION}")
    partial = (await world.objects.get(scope, head.files["notes/partial-answer.md"].content)).decode("utf-8")
    assert partial.startswith("# Onyx vs PipesHub") and f"https://docs.onyx.app/page-{SECTIONS}" in partial
    handoff = json.loads(await world.objects.get(scope, head.files["notes/handoff.json"].content))
    assert handoff["status"] == "stopped_early"


async def test_wp81_cut_3_without_a_wrap_up_the_written_report_is_still_the_result() -> None:
    world = _world(None)
    await _run(world)
    answer = await _answer(world)
    assert answer.startswith("# Onyx vs PipesHub") and "The full report is in **report.md**." in answer


def _file(path: str, size: int) -> WorkspaceFile:
    return WorkspaceFile(path, ObjectRef(f"o-{path}", "h", size), "text/markdown")


def written_deliverable(request: str, files: dict[str, WorkspaceFile]) -> str | None:
    """The engine's file for the request, read from the run's ledger (ADR-RUN-01 D1)."""
    deliverable = resolve_deliverable(request)
    written = OutcomeLedger(workspace_artifacts(files, 1, deliverable)).written(deliverable)
    return written.path if written is not None else None


def test_wp81_cut_4_which_file_is_the_engines_deliverable() -> None:
    big, small = PLACEHOLDER_MAX_BYTES, PLACEHOLDER_MAX_BYTES - 1
    # The file the request names, under out/ before anywhere else.
    files = {"report.md": _file("report.md", big), "out/report.md": _file("out/report.md", big)}
    assert written_deliverable(REQUEST, files) == "out/report.md"
    assert written_deliverable(REQUEST, {"notes/report.md": _file("notes/report.md", big)}) == "notes/report.md"
    # Else the largest text file under out/, whatever the engine named it; a placeholder is none.
    files = {"out/a.md": _file("out/a.md", big), "out/b.md": _file("out/b.md", big + 9), "out/c.png": _file("out/c.png", big * 9)}
    assert written_deliverable(REQUEST, files) == "out/b.md"
    assert written_deliverable(REQUEST, {"out/report.md": _file("out/report.md", small)}) is None
    # A request for no file has no deliverable: the wrap-up still answers from the results.
    assert written_deliverable("Which of these are security bugs?", {"out/x.md": _file("out/x.md", big)}) is None


"""R47-JOURNEY's journey checks: the verdicts the new probes read off a stack, held to what a pass and a fail look like.

R43MCP-FU-1: the capability search on a resume path reads the probe's calls after the pause (the paused call's own
result arrives once it is answered), and on a job from the job's own journal. R44SMALL-FU-4: MR-16 (only the input over
the cap skipped and named too large), MR-17 (a cut answer names its saved file, no tool ids) and MR-18 (a sandbox link
opens the saved artifact).
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

E2E = Path(__file__).resolve().parents[5] / "deployment" / "e2e"
sys.path.insert(0, str(E2E))
import journeys  # noqa: E402

READ = "mcp_findsx_lookup_note"


def _call(call_id: str, name: str, result: Any) -> list[SimpleNamespace]:  # noqa: ANN401
    return [SimpleNamespace(type="TOOL_CALL_START", data={"toolCallId": call_id, "toolCallName": name}),
            SimpleNamespace(type="TOOL_CALL_RESULT", data={"toolCallId": call_id, "content": json.dumps(result)})]


def _card() -> SimpleNamespace:
    return SimpleNamespace(type="CUSTOM", name="approval_requested", data={"value": {"interactionId": "i1"}})


def test_after_pause_drops_the_paused_calls_and_their_late_result() -> None:
    pause = _call("p1", "search_tools", {"matches": [{"name": "mcp_e2esaas_create_ticket"}]})
    ticket_start = SimpleNamespace(type="TOOL_CALL_START", data={"toolCallId": "p2", "toolCallName": "call_tool"})
    ticket_result = SimpleNamespace(type="TOOL_CALL_RESULT", data={"toolCallId": "p2", "content": "made"})
    probe = _call("c1", "search_tools", {"matches": [{"name": READ}]}) + _call("c2", "call_tool", {"text": "note"})
    frames = [*pause, ticket_start, _card(), ticket_result, *probe]
    assert journeys.find_read_verdict(frames, READ)[1] is False
    assert journeys.find_read_verdict(journeys.after_pause(frames), READ) == ("search_tools", True, [])


def test_after_pause_keeps_every_frame_of_a_run_that_never_paused() -> None:
    frames = _call("c1", "fetch_tools", {"tools": [{"name": READ}]})
    assert journeys.after_pause(frames) == frames


def _intent(run: str, tool: str) -> dict[str, Any]:
    return {"type": "tool.intent", "runId": run, "data": {"toolId": tool, "invocationId": tool}}


def test_a_job_that_read_with_no_knowledge_tool_passes() -> None:
    # R50JFIX-9: the gateway answers the engine's search_tools itself and journals no intent for it.
    read = f"mcp@i1/{journeys.READ_TOOL}"
    events = [_intent("parent", "code_task"), _intent("child", read)]
    assert journeys.job_find_read_verdict(events, "child", READ) == (read, True, [])


def test_a_job_that_searched_knowledge_only_fails() -> None:
    events = [_intent("child", "search_internal_knowledge"), _intent("child", "knowledgegraph__search")]
    first, ok, knowledge = journeys.job_find_read_verdict(events, "child", READ)
    assert (first, ok) == ("search_internal_knowledge", False)
    assert knowledge == ["search_internal_knowledge", "knowledgegraph__search"]


def test_a_job_that_searched_knowledge_before_the_read_names_it() -> None:
    events = [_intent("child", "knowledgegraph__search"), _intent("child", READ)]
    assert journeys.job_find_read_verdict(events, "child", READ) == ("knowledgegraph__search", True, ["knowledgegraph__search"])


def test_a_job_that_never_read_fails() -> None:
    assert journeys.job_find_read_verdict([_intent("parent", READ)], "child", READ)[1] is False


def test_a_cut_answer_naming_its_file_by_plain_labels_passes() -> None:
    shown = "I stopped at the spending limit. What it had gathered so far: Saved notes-1.md; Searched knowledge."
    assert journeys.cut_names(shown, "notes-1.md") == (True, [])


def test_a_cut_answer_listing_tool_ids_fails() -> None:
    shown = "What it had gathered so far: artifacts__save_artifact, knowledgegraph__search"
    named, ids = journeys.cut_names(shown, "notes-1.md")
    assert not named
    assert ids == ["artifacts__save_artifact", "knowledgegraph__search"]


def _message(text: str) -> dict[str, Any]:
    ref = "inline:" + base64.b64encode(text.encode()).decode()
    return {"type": "message.completed", "data": {"role": "assistant", "textRef": {"ref": ref}}}


def test_a_sandbox_link_rewritten_to_the_saved_file_passes() -> None:
    events = [_message("Done. [Download fib1.py](/record/abc)"),
              {"type": "x.pipeshub.answer.finalized", "data": {"answer": "Done. [Download fib1.py](/record/abc)"}}]
    assert journeys.sandbox_link_verdict(events, "fib1.py")[0] is True


def test_a_sandbox_link_left_as_is_fails() -> None:
    events = [_message("Done. [Download fib1.py](/record/abc)"),
              {"type": "x.pipeshub.tool.result_presented", "data": {"content": "[fib1.py](sandbox:/mnt/data/out/fib1.py)"}}]
    ok, detail = journeys.sandbox_link_verdict(events, "fib1.py")
    assert not ok and "tool_result" in detail


def test_r50jfix2_the_root_is_read_as_streamed_not_its_raw_transcript() -> None:
    raw = {**_message("Done. [Download fib1.py](sandbox:/mnt/data/out/fib1.py)"), "runId": "root"}
    frames = [SimpleNamespace(type="TEXT_MESSAGE_CONTENT", data={"messageId": "m1", "delta": d})
              for d in ("Done. [Download fib1", ".py](/record/abc)")]
    assert journeys.sandbox_link_verdict([raw], "fib1.py", frames, "root")[0] is True
    leaked = [SimpleNamespace(type="TEXT_MESSAGE_CONTENT", data={"messageId": "m1", "delta": "[fib1.py](sandbox:/x)"})]
    ok, detail = journeys.sandbox_link_verdict([raw], "fib1.py", leaked, "root")
    assert not ok and "streamed" in detail


def test_a_link_nobody_named_fails() -> None:
    assert journeys.sandbox_link_verdict([_message("Done.")], "fib1.py")[0] is False


def _over_cap_events(skipped: list[str], items: list[tuple[str, str]], read: bool, answer: str) -> list[dict[str, Any]]:
    return [
        {"type": "x.pipeshub.artifact.presented", "data": {"fileName": "plan-1.txt", "artifactId": "a1"}},
        {"type": "child.spawned", "runId": "r2", "data": {"childRunId": "c2"}},
        {"type": "turn.started", "runId": "c2", "data": {"skippedInputs": [{"name": n} for n in skipped]}},
        {"type": "x.pipeshub.engine.progress", "runId": "c2", "data": {"text": "code-1\n" if read else "x"}},
        {"type": "x.pipeshub.answer.finalized", "runId": "r2", "data": {
            "answer": answer, "limits": {"items": [{"kind": k, "file": f} for k, f in items]}}},
    ]


def test_only_the_input_over_the_cap_skipped_and_named_too_large_passes() -> None:
    events = _over_cap_events(["plan-1.txt"], [("attachment.too_large", "plan-1.txt")], True,
                              "I could not read plan-1.txt: it is over the size limit.")
    assert journeys.over_cap_verdict(events, "r2", "plan-1.txt", "code-1")[0] is True
    assert journeys.presented_artifacts(events, ("plan-1.txt",)) == {"plan-1.txt": "a1"}


def test_both_inputs_refused_as_unavailable_fails() -> None:
    """R46FIX-2's symptom on the lane: both files refused, the answer naming one '(unnamed)'."""
    events = _over_cap_events(["plan-1.txt", "small-1.txt"], [("attachment.unavailable", "")], False,
                              "I couldn't read the attached file (unnamed).")
    assert journeys.over_cap_verdict(events, "r2", "plan-1.txt", "code-1")[0] is False


def test_the_new_cells_are_wired_to_their_probes() -> None:
    assert journeys.consistent() == []
    for path in ("job.codex", "resume.approval", "recovery", "surface.slack"):
        assert path in journeys.SUPPORT["mcp.find_read"]
    for path in ("agent.deep", "job.opencode", "surface.mcp"):
        assert path in journeys.SUPPORT["steering"]
    for turn, path, shape in journeys.ALONE_CELLS:
        assert turn in journeys.TURNS and shape in journeys.DRIVEN[path]
    assert set(journeys.OVER_CAP_CELLS) == {(p, s) for _, p, s in journeys.ALONE_CELLS}

"""PF-3..PF-6 (U1), SR-18: what an engine run streams to the chat besides progress lines.

Command output and exit codes, and each workspace revision the controller saves with its changed files as
bounded diffs. The frames keep the ``code_task`` tool tag journals already hold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.engines.progress import (
    MAX_DIFF_CHARS,
    MAX_DIFF_FILES,
    MAX_DIFF_TEXT_BYTES,
    MAX_OUTPUT_CHARS,
    PROGRESS_TOOL,
    EngineProgress,
    frame,
    progress_for,
    revision_progress,
)
from harness_core.engines.events import CommandFinished, CommandOutput, EngineEventMeta
from harness_core.testing.clock import ManualClock
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.types import ObjectScope, WorkspaceFile, WorkspaceManifest

if TYPE_CHECKING:
    from harness_core.workspace.changes import CommitReason

ORG, SESSION = "org-1", "conv-1"
WORKSPACE = f"ws:{SESSION}"
META = EngineEventMeta("trn_1", 0)


def test_sr_18_a_frame_keeps_the_tool_tag_journals_hold() -> None:
    shown = EngineProgress("file", "out/a.csv", {"change": "added"})
    assert PROGRESS_TOOL == "code_task"
    assert frame("run-1", shown) == {
        "tool": "code_task", "taskId": "run-1", "kind": "file", "text": "out/a.csv", "data": {"change": "added"},
    }


def test_pf_3_command_output_is_bounded_and_the_exit_code_shown() -> None:
    shown = progress_for(CommandOutput(META, "c1", "ok\n", "stderr"))
    assert shown == EngineProgress("output", "ok\n", {"callId": "c1", "stream": "stderr", "truncated": False})
    long = progress_for(CommandOutput(META, "c1", "x" * (MAX_OUTPUT_CHARS + 10)))
    assert long is not None and len(long.text) == MAX_OUTPUT_CHARS and long.data["truncated"] is True
    done = progress_for(CommandFinished(META, "c1", 2, "failed"))
    assert done == EngineProgress("command_done", "", {"callId": "c1", "exitCode": 2, "status": "failed"})


class Workspace:
    def __init__(self) -> None:
        self.objects = InMemoryBinaryObjectStore()
        self.store = InMemoryWorkspaceStore(clock=ManualClock())
        self.frames: list[EngineProgress] = []

    async def save(self, files: dict[str, bytes], deletes: frozenset[str] = frozenset()) -> WorkspaceManifest:
        head = await self.store.head(ObjectScope(ORG), WORKSPACE)
        entries: list[WorkspaceFile] = []
        for path, content in files.items():
            ref = await self.objects.put(ObjectScope(ORG, WORKSPACE), content)
            entries.append(WorkspaceFile(path, ref, "text/plain"))
        return await self.store.commit(
            ObjectScope(ORG), WORKSPACE, base_revision=head.revision,
            changes=ChangeSet.of(*entries, deletes=deletes), reason="turn_end",
        )

    async def show(self, manifest: WorkspaceManifest, reason: CommitReason) -> None:
        parent = (
            {}
            if manifest.parent_revision is None
            else (await self.store.revision(ObjectScope(ORG), WORKSPACE, manifest.parent_revision)).files
        )
        self.frames.extend(
            await revision_progress(parent, manifest, reason, lambda ref: self.objects.get(ObjectScope(ORG, WORKSPACE), ref))
        )

    def of(self, kind: str) -> list[EngineProgress]:
        return [f for f in self.frames if f.kind == kind]


async def test_pf_4_a_saved_revision_is_a_version_and_one_diff_per_changed_file() -> None:
    ws = Workspace()
    _ = await ws.save({"in/data.csv": b"a,b\n1,2\n"})
    second = await ws.save({"out/report.md": b"# Report\nRevenue 42\n", "notes.txt": b"one\ntwo\n"})
    await ws.show(second, "autosave")
    third = await ws.save({"notes.txt": b"one\nthree\n"}, deletes=frozenset({"out/report.md"}))
    await ws.show(third, "turn_end")
    versions = ws.of("version")
    assert [v.data for v in versions] == [
        {"revision": second.revision, "reason": "autosave", "files": 3, "changed": 2, "omitted": 0},
        {"revision": third.revision, "reason": "turn_end", "files": 2, "changed": 2, "omitted": 0},
    ]
    diffs = [(d.text, d.data["change"]) for d in ws.of("diff")]
    assert diffs == [
        ("notes.txt", "added"), ("out/report.md", "added"), ("notes.txt", "modified"), ("out/report.md", "deleted"),
    ]
    modified: dict[str, Any] = dict(ws.of("diff")[2].data)
    assert modified["diff"].splitlines() == [
        "--- a/notes.txt", "+++ b/notes.txt", "@@ -1,2 +1,2 @@", " one", "-two", "+three",
    ]
    assert modified["truncated"] is False and modified["sizeBytes"] == len(b"one\nthree\n")
    assert "in/data.csv" not in [d.text for d in ws.of("diff")]


async def test_pf_5_binary_oversized_and_long_diffs() -> None:
    ws = Workspace()
    saved = await ws.save({
        "out/chart.png": b"\x89PNG\x00\x01",
        "out/big.txt": b"x" * (MAX_DIFF_TEXT_BYTES + 1),
        "out/long.txt": "".join(f"line {i}\n" for i in range(5_000)).encode(),
        "out/latin.txt": "caf\xe9".encode("latin-1"),
    })
    await ws.show(saved, "turn_end")
    by_path = {d.text: dict(d.data) for d in ws.of("diff")}
    assert by_path["out/chart.png"]["binary"] is True and "diff" not in by_path["out/chart.png"]
    assert by_path["out/latin.txt"]["binary"] is True
    assert by_path["out/big.txt"]["tooLarge"] is True and "diff" not in by_path["out/big.txt"]
    assert by_path["out/long.txt"]["truncated"] is True and len(by_path["out/long.txt"]["diff"]) == MAX_DIFF_CHARS


async def test_pf_6_at_most_twenty_files_per_revision_the_rest_counted() -> None:
    ws = Workspace()
    saved = await ws.save({f"out/f{i:02}.txt": b"x\n" for i in range(MAX_DIFF_FILES + 5)})
    await ws.show(saved, "turn_end")
    assert len(ws.of("diff")) == MAX_DIFF_FILES
    assert ws.of("version")[0].data["omitted"] == 5


def test_ctl4_a_tool_line_carries_the_tool_s_display_name_when_the_run_knows_it() -> None:
    """CTL-4: the chat names an engine's tool step as the loop names it (its display name), not by its machine name."""
    from app.harness.engines.presenter import PROGRESS, PipesHubEngineRunPresenter
    from harness_core.engines.durable.ports import ProgressStamp
    from harness_core.engines.events import EngineEventMeta, ToolCallSeen

    names = {"knowledgegraph__fetch_record": "Read the full record"}
    presenter = PipesHubEngineRunPresenter(display_name=names.get)
    stamp = ProgressStamp("run-1", "trn_1", iter(range(10)).__next__)
    meta = EngineEventMeta(turn_id="trn_1", seq=1)
    (known,) = presenter.event(stamp, ToolCallSeen(meta, "c1", "knowledgegraph__fetch_record", "started"))
    (unknown,) = presenter.event(stamp, ToolCallSeen(meta, "c2", "mystery_tool", "started"))
    assert known.type == PROGRESS
    assert known.data["data"] == {"callId": "c1", "displayName": "Read the full record"}
    assert unknown.data["data"] == {"callId": "c2"}


def _stamp() -> Any:  # noqa: ANN401
    from itertools import count

    from harness_core.engines.durable.ports import ProgressStamp

    n = count()
    return ProgressStamp("run-1", "trn_1", lambda: next(n))


def _shown(events: list[Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [(e.data["kind"], e.data["text"], e.data["data"]) for e in events]


def test_ctresults_a_tool_step_reports_how_it_ended() -> None:
    """A tool step reports how it ended: with only its start in the chat, a failed gateway call read as done."""
    from harness_core.engines.events import ToolCallSeen

    assert progress_for(ToolCallSeen(META, "t1", "jira_search", "failed")) == EngineProgress(
        "tool_result", "jira_search", {"callId": "t1", "status": "failed"}
    )


def test_ctresults_a_gateway_result_lands_on_its_step_redacted_and_bounded() -> None:
    from app.harness.engines.presenter import PipesHubEngineRunPresenter
    from harness_core.engines.events import ToolCallSeen
    from harness_core.gateway.types import McpResult

    presenter = PipesHubEngineRunPresenter()
    stamp = _stamp()
    presenter.event(stamp, ToolCallSeen(META, "t1", "lookup_note", "started"))
    token = "ghp_" + "a" * 36
    result = McpResult(f"note: due Friday {token} " + "x" * 5000, is_error=False, disposition="executed", retryable=False)
    [(kind, text, data)] = _shown(presenter.tool_result(stamp, "lookup_note", {}, result))
    assert (kind, text, data["callId"], data["status"], data["truncated"]) == ("tool_result", "lookup_note", "t1", "completed", True)
    assert data["result"].startswith("note: due Friday") and token not in data["result"]
    assert len(data["result"]) <= 4_000


def test_ctresults_a_result_before_its_step_waits_for_it_and_lazy_calls_name_their_tool() -> None:
    from app.harness.engines.presenter import PipesHubEngineRunPresenter
    from harness_core.engines.events import ToolCallSeen
    from harness_core.gateway.types import McpResult

    presenter = PipesHubEngineRunPresenter()
    stamp = _stamp()
    failed = McpResult("not found", is_error=True, disposition="executed", retryable=False)
    assert presenter.tool_result(stamp, "call_tool", {"name": "jira_get", "arguments": {}}, failed) == []
    shown = _shown(presenter.event(stamp, ToolCallSeen(META, "t9", "jira_get", "started")))
    assert [(k, d.get("callId"), d.get("status")) for k, _, d in shown] == [("tool", "t9", None), ("tool_result", "t9", "failed")]


def test_r31journey_fu1_a_last_line_without_a_newline_reaches_the_person() -> None:
    """R31JOURNEY-FU-1: ``printf x`` (or ``cat`` of a file written without one) showed nothing on any engine. The
    redactor holds a line until its newline; the tail it gave back when the command finished was fed to a new one,
    which held it again, so it was dropped."""
    from app.harness.engines.presenter import PipesHubEngineRunPresenter

    presenter = PipesHubEngineRunPresenter()
    stamp = _stamp()
    assert presenter.event(stamp, CommandOutput(META, "c1", "x")) == []
    assert presenter.event(stamp, CommandOutput(META, "c1", "warn", "stderr")) == []
    shown = _shown(presenter.event(stamp, CommandFinished(META, "c1", 0, "completed")))
    assert [(k, t, d.get("stream")) for k, t, d in shown] == [
        ("output", "x", "stdout"), ("output", "warn", "stderr"), ("command_done", "", None),
    ]
    assert presenter._shown_output == {}, "nothing is kept for a finished command"


def test_r31journey_fu1_a_held_secret_is_still_masked_at_the_end() -> None:
    from app.harness.engines.presenter import PipesHubEngineRunPresenter

    presenter = PipesHubEngineRunPresenter()
    stamp = _stamp()
    token = "ghp_" + "a" * 36
    assert presenter.event(stamp, CommandOutput(META, "c1", f"token {token}")) == []
    [(kind, text, _), _done] = _shown(presenter.event(stamp, CommandFinished(META, "c1", 0, "completed")))
    assert kind == "output" and token not in text and text.startswith("token ")

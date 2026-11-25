"""FU2-2: what kind of change a completed ACP write reports, as opencode 1.18.34 sends it over ACP.

opencode's ``write`` tool sends no diff block (its ACP diff needs ``oldString``), only a location, and says whether
the file existed in ``rawOutput.metadata.exists``; ``apply_patch`` lists each file with its ``type`` in
``rawOutput.metadata.files``. The workspace diff the chat shows beside it calls a new file ``added``, so the
engine's own line must too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.events import FileChanged
from harness_engines.acp.mapper import AcpEventMapper
from harness_engines.acp.permissions import SessionPolicy

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue

POLICY = SessionPolicy(
    agent="opencode",
    allowed=frozenset(),
    native_names={},
    plan_tool=None,
    gateway_prefix="pipeshub_",
    mcp_server="pipeshub",
    tools_url="",
    tools_placeholder="",
    client_fs_read=False,
    client_fs_write=False,
)


def changes(tool: str, raw_input: dict[str, JsonValue], completed: dict[str, JsonValue]) -> list[tuple[str, str]]:
    mapper = AcpEventMapper(turn_id="t-1", policy=POLICY)
    path = raw_input.get("filePath")
    start: dict[str, JsonValue] = {
        "sessionUpdate": "tool_call",
        "toolCallId": "c1",
        "title": tool,
        "kind": "edit",
        "status": "pending",
        "locations": [{"path": path}] if isinstance(path, str) else [],
        "rawInput": raw_input,
    }
    events = mapper.update(start)
    events += mapper.update({"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed", **completed})
    return [(e.path, e.change) for e in events if isinstance(e, FileChanged)]


def test_opencode_write_of_a_new_file_is_added() -> None:
    write = {"filePath": "/workspace/notes.md", "content": "hello"}
    done: dict[str, JsonValue] = {
        "content": [{"type": "content", "content": {"type": "text", "text": "Wrote file successfully."}}],
        "rawOutput": {"output": "Wrote file successfully.", "metadata": {"filepath": "/workspace/notes.md", "exists": False}},
    }
    assert changes("write", write, done) == [("notes.md", "added")]


def test_opencode_write_over_an_existing_file_is_modified() -> None:
    write = {"filePath": "/workspace/notes.md", "content": "hello"}
    done: dict[str, JsonValue] = {"rawOutput": {"output": "ok", "metadata": {"filepath": "/workspace/notes.md", "exists": True}}}
    assert changes("write", write, done) == [("notes.md", "modified")]


def test_opencode_edit_keeps_its_diff_and_is_modified() -> None:
    edit = {"filePath": "/workspace/notes.md", "oldString": "a", "newString": "b"}
    done: dict[str, JsonValue] = {
        "content": [{"type": "diff", "path": "/workspace/notes.md", "oldText": "a", "newText": "b"}],
        "rawOutput": {"output": "Edit applied successfully.", "metadata": {}},
    }
    assert changes("edit", edit, done) == [("notes.md", "modified")]


def test_opencode_apply_patch_reports_each_file_by_its_type() -> None:
    patch = {"patchText": "*** Begin Patch\n*** Add File: new.md\n+x\n*** End Patch"}
    files: list[JsonValue] = [
        {"filePath": "/workspace/new.md", "type": "add"},
        {"filePath": "/workspace/old.md", "type": "update"},
        {"filePath": "/workspace/gone.md", "type": "delete"},
    ]
    done: dict[str, JsonValue] = {"rawOutput": {"output": "Success.", "metadata": {"files": files}}}
    assert changes("apply_patch", patch, done) == [("new.md", "added"), ("old.md", "modified"), ("gone.md", "deleted")]


def test_a_spec_diff_without_old_text_is_still_added() -> None:
    write = {"filePath": "/workspace/notes.md", "content": "hello"}
    done: dict[str, JsonValue] = {"content": [{"type": "diff", "path": "/workspace/notes.md", "oldText": None, "newText": "hello"}]}
    assert changes("write", write, done) == [("notes.md", "added")]

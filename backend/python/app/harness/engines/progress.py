"""What an engine run shows the chat while it runs (S1, U1, E1): progress lines, command output, and each
workspace revision the controller saves with its changed files as bounded diffs.

Every frame comes from an engine event or a saved manifest; the chat renders them (``frontend/app/(main)/chat/
code-tasks.ts``). Bounds are applied here so a chatty engine cannot flood the stream: one command's output, one
file's diff and the number of files per revision are capped, and the cap is reported (``truncated``, ``omitted``).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    FileChanged,
    RepositoryReady,
    SignInProgress,
    SubAgentFinished,
    SubAgentStarted,
    ToolCallSeen,
    ToolCallUpdated,
)
from harness_core.workspace.changes import diff_files

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.artifacts.types import ArtifactCard
    from harness_core.engines.events import EngineEvent
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.types import ObjectRef, WorkspaceFile, WorkspaceManifest

ProgressKind = Literal[
    "started",
    "finished",
    "command",
    "output",
    "command_done",
    "file",
    "tool",
    "tool_result",
    "tool_update",
    "helper",
    "artifact",
    "version",
    "diff",
    "approval",
    "signin",
    "repository",
]

MAX_OUTPUT_CHARS = 4_000
SHOWN_GOAL_CHARS = 2_000
SHOWN_OUTPUT_CHARS = 4_000
# A helper's row names its task in one line.
SHOWN_HELPER_TASK_CHARS = 200
# The frame's tool tag: journals of engine runs hold it and Node's projector and the browser key on it, so it keeps
# the name of the tool that introduced the card.
PROGRESS_TOOL = "code_task"
MAX_DIFF_CHARS = 16_000
MAX_DIFF_TEXT_BYTES = 256 * 1024
MAX_DIFF_FILES = 20


@dataclass(frozen=True, slots=True)
class EngineProgress:
    """One progress line for the chat (U1 renders the card); ``data`` is the structured part."""

    kind: ProgressKind
    text: str
    data: Mapping[str, Any] = field(default_factory=lambda: dict[str, Any]())


def bounded(text: str, limit: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


def started_progress(goal: str) -> EngineProgress:
    return EngineProgress("started", bounded(goal, SHOWN_GOAL_CHARS)[0])


def finished_progress(
    *, success: bool, reason: str | None, output: str, usage: Mapping[str, Any] | None = None
) -> EngineProgress:
    data: dict[str, Any] = {"success": success}
    if reason is not None:
        data["reason"] = reason
    if usage is not None:
        data["usage"] = dict(usage)
    return EngineProgress("finished", bounded(output, SHOWN_OUTPUT_CHARS)[0], data)


def artifact_progress(card: ArtifactCard) -> EngineProgress:
    return EngineProgress(
        "artifact",
        card.name,
        {
            "artifactId": card.artifact_id,
            "version": card.version,
            "mimeType": card.mime_type,
            "sizeBytes": card.size_bytes,
            "lifecycle": card.lifecycle,
        },
    )


def tool_result_progress(tool: str, call_id: str, result: str, *, failed: bool) -> EngineProgress:
    """What a gateway tool returned, for the step the engine started under ``call_id``, bounded like a command's output."""
    text, cut = bounded(result, SHOWN_OUTPUT_CHARS)
    status = "failed" if failed else "completed"
    return EngineProgress("tool_result", tool, {"callId": call_id, "status": status, "result": text, "truncated": cut})


def frame(task_id: str, shown: EngineProgress) -> dict[str, Any]:
    """The ``engine_progress`` value the chat's card reads, on either stream."""
    return {"tool": PROGRESS_TOOL, "taskId": task_id, "kind": shown.kind, "text": shown.text, "data": dict(shown.data)}


def progress_for(event: EngineEvent) -> EngineProgress | None:
    """The engine events a user sees move; text deltas, usage and turn ends are not progress lines. A step one of
    the engine's own helpers took names it (``subSessionId``), so the chat nests it under that helper (EV-07)."""
    shown = _progress_for(event)
    helper = getattr(event, "sub_session_id", None)
    if shown is None or helper is None or isinstance(event, (SubAgentStarted, SubAgentFinished)):
        return shown
    return EngineProgress(shown.kind, shown.text, {**shown.data, "subSessionId": helper})


def _progress_for(event: EngineEvent) -> EngineProgress | None:
    match event:
        case CommandStarted():
            started: dict[str, Any] = {"callId": event.call_id}
            if event.output_may_be_lost:
                started["outputMayBeLost"] = True
            return EngineProgress("command", event.command, started)
        case CommandOutput():
            text, cut = bounded(event.text, MAX_OUTPUT_CHARS)
            return EngineProgress("output", text, {"callId": event.call_id, "stream": event.stream, "truncated": cut})
        case CommandFinished():
            return EngineProgress(
                "command_done", "", {"callId": event.call_id, "exitCode": event.exit_code, "status": event.status}
            )
        case FileChanged():
            return EngineProgress("file", event.path, {"change": event.change})
        case ToolCallSeen(status="started"):
            return EngineProgress("tool", event.tool, {"callId": event.call_id})
        case ToolCallUpdated():
            return EngineProgress("tool_update", event.tool, {"callId": event.call_id})
        case ToolCallSeen():
            # Without it a tool step only ever read as started: the chat guessed it had ended from what came next.
            return EngineProgress("tool_result", event.tool, {"callId": event.call_id, "status": event.status})
        case SubAgentStarted():
            started: dict[str, Any] = {"subSessionId": event.sub_session_id, "callId": event.call_id, "status": "started"}
            if event.task:
                started["task"] = bounded(event.task, SHOWN_HELPER_TASK_CHARS)[0]
            return EngineProgress("helper", event.agent, started)
        case SubAgentFinished():
            # A helper's end closes its group in the chat; before it, a helper read as running until the run ended.
            return EngineProgress(
                "helper", "", {"subSessionId": event.sub_session_id, "callId": event.call_id, "status": event.status}
            )
        case SignInProgress():
            # The chat's sign-in card (SIGNIN): ids, state, reason and plan kind; the link is asked for by the owner.
            data: dict[str, Any] = {"attemptId": event.attempt_id, "state": event.state}
            if event.reason_code is not None:
                data["reasonCode"] = event.reason_code
            if event.plan is not None:
                data["plan"] = event.plan
            return EngineProgress("signin", "", data)
        case RepositoryReady():
            return EngineProgress("repository", event.repository, {"action": "copied", "branch": event.branch})
        case _:
            return None


def _text_of(content: bytes) -> str | None:
    if b"\x00" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def unified_diff(path: str, before: str, after: str) -> tuple[str, bool]:
    """A unified diff of one file, bounded; every line ends with a newline so the chat can split it."""
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""
    )
    return bounded("".join(f"{line}\n" for line in lines), MAX_DIFF_CHARS)


async def _file_diff(
    path: str,
    before: WorkspaceFile | None,
    after: WorkspaceFile | None,
    read: Callable[[ObjectRef], Awaitable[bytes]],
) -> EngineProgress:
    change = "added" if before is None else "deleted" if after is None else "modified"
    current = after or before
    assert current is not None
    data: dict[str, Any] = {"change": change, "sizeBytes": current.content.size_bytes, "mimeType": current.mime_type}
    if any(side is not None and side.content.size_bytes > MAX_DIFF_TEXT_BYTES for side in (before, after)):
        data["tooLarge"] = True
        return EngineProgress("diff", path, data)
    old = _text_of(await read(before.content)) if before is not None else ""
    new = _text_of(await read(after.content)) if after is not None else ""
    if old is None or new is None:
        data["binary"] = True
        return EngineProgress("diff", path, data)
    data["diff"], data["truncated"] = unified_diff(path, old, new)
    return EngineProgress("diff", path, data)


async def revision_progress(
    parent: Mapping[str, WorkspaceFile],
    manifest: WorkspaceManifest,
    reason: CommitReason,
    read: Callable[[ObjectRef], Awaitable[bytes]],
) -> list[EngineProgress]:
    """The ``version`` frame of a saved revision, then one ``diff`` frame per changed file (at most 20)."""
    changes = diff_files(parent, manifest.files)
    paths = sorted(changes.paths)
    shown = paths[:MAX_DIFF_FILES]
    frames = [
        EngineProgress(
            "version",
            str(manifest.revision),
            {
                "revision": manifest.revision,
                "reason": reason,
                "files": len(manifest.files),
                "changed": len(paths),
                "omitted": len(paths) - len(shown),
            },
        )
    ]
    frames.extend([await _file_diff(path, parent.get(path), manifest.files.get(path), read) for path in shown])
    return frames


__all__ = [
    "SHOWN_GOAL_CHARS",
    "SHOWN_HELPER_TASK_CHARS",
    "SHOWN_OUTPUT_CHARS",
    "MAX_DIFF_CHARS",
    "MAX_DIFF_FILES",
    "MAX_DIFF_TEXT_BYTES",
    "MAX_OUTPUT_CHARS",
    "PROGRESS_TOOL",
    "EngineProgress",
    "ProgressKind",
    "artifact_progress",
    "bounded",
    "finished_progress",
    "frame",
    "progress_for",
    "revision_progress",
    "started_progress",
    "tool_result_progress",
    "unified_diff",
]

"""The chat's workspace as the run's own loop sees it (LONGTASK-F6).

``run_code`` reads and writes the workspace under ``$WORKSPACE_DIR``; in a react or auto chat it is reachable only
through ``coding_agent``, and nothing told the loop what the workspace held. Asked on a later day to "continue
where we left off", an agent looked for its own notes through knowledge search and then asked the person.

* ``WorkspaceViewStep`` (pre-run step ``workspace``) renders the workspace's index once per run: its files and, when
  the wiki has one, its index page. It is journaled as a slot, so every attempt and every turn sends the same text.
* ``WorkspaceContextShaper`` / ``WorkspaceContextHost`` put that text right before the run's input, as recalled
  memories are put (data, not the system prompt).
* ``list_workspace_files`` and ``read_workspace_file`` read the workspace's head directly: a read of the chat's own
  files, which never asks.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final, cast, override

from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.tags import EFFECT_TAG_KEY

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from app.harness.assembly.run_code_workspace import RunCodeWorkspace
    from harness_core.context.types import HostBands
    from harness_core.kernel.durable.context_compiler import ContextHost
    from harness_core.kernel.durable.ports import ModelCall, TurnShaper
    from harness_core.kernel.fold import RunState
    from harness_core.logs import HarnessLog

WORKSPACE_STEP: Final = "workspace"
WORKSPACE_SLOT: Final = PRE_RUN_SLOT_PREFIX + WORKSPACE_STEP
LIST_TOOL: Final = "list_workspace_files"
READ_TOOL: Final = "read_workspace_file"
# The pages the index shows in full, the first that exists: a wiki's own index, else a top-level one.
INDEX_PAGES: Final = ("wiki/index.md", "index.md", "README.md")
MAX_LISTED: Final = 200
MAX_INDEX_CHARS: Final = 4_000
MAX_READ_CHARS: Final = 50_000


async def workspace_index(workspace: RunCodeWorkspace) -> str:
    """The workspace as one note: its files with their sizes and, when there is one, the index page in full."""
    files = await workspace.listing()
    if not files:
        return ""
    lines = [
        "This chat's workspace (files you and the person kept; `$WORKSPACE_DIR` in run_code, and "
        f"`{READ_TOOL}` reads one here) holds {len(files)} file(s):"
    ]
    lines += [f"- {path} ({size} bytes)" for path, size in files[:MAX_LISTED]]
    if len(files) > MAX_LISTED:
        lines.append(f"- … and {len(files) - MAX_LISTED} more ({LIST_TOOL} lists them all)")
    page = next((p for p in INDEX_PAGES if p in dict(files)), None)
    if page is not None:
        data = await workspace.read(page) or b""
        text = data.decode("utf-8", "replace")
        cut = text[:MAX_INDEX_CHARS] + (" […]" if len(text) > MAX_INDEX_CHARS else "")
        lines += ["", f"{page}:", cut]
    return "\n".join(lines)


def workspace_text(slots: Mapping[str, JsonValue]) -> str:
    slot = slots.get(WORKSPACE_SLOT)
    text = slot.get("text") if isinstance(slot, dict) else None
    return text if isinstance(text, str) else ""


class WorkspaceViewStep:
    """Pre-run step ``workspace``: the index, rendered once and journaled."""

    key = WORKSPACE_STEP

    def __init__(self, workspace: RunCodeWorkspace, log: HarnessLog) -> None:
        self._workspace = workspace
        self._log = log

    async def run(self, input_text: str) -> JsonValue:
        try:
            text = await workspace_index(self._workspace)
        except Exception as exc:  # a run without its index is still the person's run
            self._log.warning("workspace.index_unavailable", errorType=type(exc).__name__)
            return {"text": "", "unavailable": True}
        self._log.info("workspace.indexed", chars=len(text))
        return {"text": text}


class WorkspaceContextShaper:
    """Wraps the turn shaper; adds the workspace's index before the run's input."""

    def __init__(self, inner: TurnShaper) -> None:
        self._inner = inner

    async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall:
        shaped = await self._inner.shape(call, turn, state)
        text = workspace_text(shaped.slots)
        if not text:
            return shaped
        messages = list(shaped.messages)
        at = shaped.input_index if shaped.input_index is not None else 0
        messages.insert(at, UserMessage(content=text, injected=True))
        return replace(shaped, messages=messages, input_index=at + 1 if shaped.input_index is not None else None)

    async def enter(self, turn: int, state: RunState) -> None:
        await self._inner.enter(turn, state)

    def __getattr__(self, name: str) -> object:
        return cast("object", getattr(self._inner, name))


class WorkspaceContextHost:
    """With the context compiler on: the index is part of the volatile tail, as recalled memories are."""

    def __init__(self, inner: ContextHost) -> None:
        self._inner = inner

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands:
        bands = await self._inner.bands(call, turn, state)
        text = workspace_text({**state.slots, **call.slots})
        return replace(bands, tail=(text, *bands.tail)) if text else bands

    def __getattr__(self, name: str) -> object:
        return cast("object", getattr(self._inner, name))


class _WorkspaceTool(Tool):
    def __init__(self, workspace: RunCodeWorkspace) -> None:
        self._workspace = workspace

    @property
    @override
    def tags(self) -> list[Tag]:
        return [Tag(key=EFFECT_TAG_KEY, value="read"), Tag("category", "read")]


class ListWorkspaceFilesTool(_WorkspaceTool):
    @property
    @override
    def name(self) -> str:
        return LIST_TOOL

    @property
    @override
    def short_description(self) -> str:
        return "List the files in this chat's workspace."

    @property
    @override
    def description(self) -> str:
        return (
            "List the files kept in this chat's workspace (what you or the person saved in earlier turns: notes, "
            "data, code), with their sizes. Use it before searching anywhere else for a file you made in this chat."
        )

    @property
    @override
    def path(self) -> str:
        return f"/toolsets/workspace/{LIST_TOOL}"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="prefix", type=ParameterType.STRING, required=False, default=None,
                description="Only paths that start with this (e.g. 'wiki/').",
            )
        ]

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        prefix = str(kwargs.get("prefix") or "")
        files = [{"path": p, "bytes": n} for p, n in await self._workspace.listing() if p.startswith(prefix)]
        return ToolOutput(success=True, data={"files": files[:MAX_LISTED], "total": len(files)})


class ReadWorkspaceFileTool(_WorkspaceTool):
    @property
    @override
    def name(self) -> str:
        return READ_TOOL

    @property
    @override
    def short_description(self) -> str:
        return "Read a file from this chat's workspace."

    @property
    @override
    def description(self) -> str:
        return (
            "Read one text file kept in this chat's workspace, by its path in the workspace (e.g. 'wiki/index.md'). "
            "To change files, or for data too large to read, use run_code under $WORKSPACE_DIR."
        )

    @property
    @override
    def path(self) -> str:
        return f"/toolsets/workspace/{READ_TOOL}"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="path", type=ParameterType.STRING, required=True, description="The file's path."),
            ToolParameter(
                name="max_chars", type=ParameterType.INTEGER, required=False, default=MAX_READ_CHARS,
                description="Read at most this many characters.",
            ),
        ]

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        path = str(kwargs.get("path") or "").lstrip("/").removeprefix("workspace/")
        data = await self._workspace.read(path)
        if data is None:
            return ToolOutput(success=False, error=f"No file {path!r} in this chat's workspace; {LIST_TOOL} lists them.")
        limit = int(kwargs.get("max_chars") or MAX_READ_CHARS)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolOutput(success=False, error=f"{path!r} is not a text file; read it with run_code.")
        return ToolOutput(success=True, data={"path": path, "content": text[:limit], "truncated": len(text) > limit})


def workspace_tools(workspace: RunCodeWorkspace | None) -> tuple[Tool, ...]:
    return () if workspace is None else (ListWorkspaceFilesTool(workspace), ReadWorkspaceFileTool(workspace))


__all__ = [
    "LIST_TOOL",
    "READ_TOOL",
    "WORKSPACE_SLOT",
    "WORKSPACE_STEP",
    "ListWorkspaceFilesTool",
    "ReadWorkspaceFileTool",
    "WorkspaceContextHost",
    "WorkspaceContextShaper",
    "WorkspaceViewStep",
    "workspace_index",
    "workspace_text",
    "workspace_tools",
]

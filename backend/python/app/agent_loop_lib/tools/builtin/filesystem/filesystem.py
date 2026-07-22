from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.types import Source
from app.agent_loop_lib.modules.providers.workspace.base import WorkspaceBackend
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)

"""Filesystem tools over a pluggable WorkspaceBackend. Read-only tools
(ls/read_file/glob/grep) carry no risk tag (default LOW); the two mutating
tools (write_file/edit_file) declare `Tag("risk", "medium")` so the
`ModeHook` middleware actually blocks them in "plan" mode — plan mode being
read-only is enforced here, not just described in the prompt.
"""


class LsTool(Tool):
    """List files and directories in the workspace."""

    def __init__(self, backend: WorkspaceBackend) -> None:
        self._backend = backend

    @property
    def name(self) -> str:
        return "ls"

    @property
    def short_description(self) -> str:
        return "List files and directories in the workspace."

    @property
    def description(self) -> str:
        return "List files and directories at a path in the workspace (\"\" for the root)."

    @property
    def path(self) -> str:
        return "/toolsets/filesystem/ls"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="path", type=ParameterType.STRING, required=False, default=None,
                description="Directory to list. Defaults to the workspace root.",
            ),
        ]

    async def execute(self, path: str = "", **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data=await self._backend.ls(path))


class ReadFileTool(Tool):
    """Read the full contents of a file in the workspace."""

    def __init__(self, backend: WorkspaceBackend) -> None:
        self._backend = backend

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def short_description(self) -> str:
        return "Read the full contents of a file in the workspace."

    @property
    def description(self) -> str:
        return "Read the full contents of a file in the workspace."

    @property
    def path(self) -> str:
        return "/toolsets/filesystem/read_file"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="path", type=ParameterType.STRING, required=True, description="Path of the file to read."),
        ]

    async def execute(self, path: str, **kwargs: Any) -> ToolOutput:
        content = await self._backend.read_file(path)
        return ToolOutput(success=True, data=content, sources=[Source(file=path)])


class WriteFileTool(Tool):
    """Create or overwrite a file in the workspace."""

    def __init__(self, backend: WorkspaceBackend) -> None:
        self._backend = backend

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def short_description(self) -> str:
        return "Create or overwrite a file in the workspace."

    @property
    def description(self) -> str:
        return "Create a new file, or overwrite an existing one, with the given content."

    @property
    def path(self) -> str:
        return "/toolsets/filesystem/write_file"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "medium"), Tag("category", "write")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="path", type=ParameterType.STRING, required=True, description="Path of the file to write."),
            ToolParameter(name="content", type=ParameterType.STRING, required=True, description="Full content to write."),
        ]

    async def execute(self, path: str, content: str, **kwargs: Any) -> ToolOutput:
        await self._backend.write_file(path, content)
        return ToolOutput(success=True, data=f"Wrote {len(content)} character(s) to {path}")


class EditFileTool(Tool):
    """Replace one exact, unique occurrence of text in an existing file."""

    def __init__(self, backend: WorkspaceBackend) -> None:
        self._backend = backend

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def short_description(self) -> str:
        return "Replace one exact occurrence of text in an existing file."

    @property
    def description(self) -> str:
        return (
            "Replace exactly one occurrence of old_text with new_text in an "
            "existing file. old_text must match exactly once — include enough "
            "surrounding context to make it unique, or the edit is rejected."
        )

    @property
    def path(self) -> str:
        return "/toolsets/filesystem/edit_file"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "medium"), Tag("category", "write")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="path", type=ParameterType.STRING, required=True, description="Path of the file to edit."),
            ToolParameter(name="old_text", type=ParameterType.STRING, required=True, description="Exact text to replace (must occur exactly once)."),
            ToolParameter(name="new_text", type=ParameterType.STRING, required=True, description="Replacement text."),
        ]

    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> ToolOutput:
        await self._backend.edit_file(path, old_text, new_text)
        return ToolOutput(success=True, data=f"Edited {path}")


class GlobTool(Tool):
    """Find files in the workspace matching a shell-style glob pattern."""

    def __init__(self, backend: WorkspaceBackend) -> None:
        self._backend = backend

    @property
    def name(self) -> str:
        return "glob"

    @property
    def short_description(self) -> str:
        return "Find files matching a shell-style glob pattern."

    @property
    def description(self) -> str:
        return "Find files matching a shell-style glob pattern (e.g. '*.py', 'src/**/*.md')."

    @property
    def path(self) -> str:
        return "/toolsets/filesystem/glob"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="pattern", type=ParameterType.STRING, required=True, description="Glob pattern to match paths against."),
        ]

    async def execute(self, pattern: str, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data=await self._backend.glob(pattern))


class GrepTool(Tool):
    """Search file contents for a regex pattern."""

    def __init__(self, backend: WorkspaceBackend) -> None:
        self._backend = backend

    @property
    def name(self) -> str:
        return "grep"

    @property
    def short_description(self) -> str:
        return "Search file contents for a regex pattern."

    @property
    def description(self) -> str:
        return "Search file contents for a regex pattern, returning matching lines with path and line number."

    @property
    def path(self) -> str:
        return "/toolsets/filesystem/grep"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="pattern", type=ParameterType.STRING, required=True, description="Regex pattern to search for."),
            ToolParameter(name="path", type=ParameterType.STRING, required=False, default=None, description="Restrict the search to this file or directory prefix."),
        ]

    async def execute(self, pattern: str, path: str | None = None, **kwargs: Any) -> ToolOutput:
        matches = await self._backend.grep(pattern, path)
        return ToolOutput(success=True, data=[m.model_dump() for m in matches])

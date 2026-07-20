from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class GrepMatch(BaseModel):
    path: str
    line_number: int
    line: str


class WorkspaceBackend(ABC):
    """Pluggable virtual filesystem the Phase 3 filesystem tools (`ls`,
    `read_file`, `write_file`, `edit_file`, `glob`, `grep` — see
    tools/builtin/filesystem/filesystem.py) operate over: in-memory (ephemeral runs,
    testing), a local directory (dev), or a sandboxed/remote FS later,
    all behind the same six operations.

    Also the natural home for the Phase 1 context engine's offload target
    (see hooks/middleware/builtin/offload.py's `OffloadStore` protocol) once wired
    together — a large tool result can be `write_file`'d here and replaced
    with a path + preview instead of living in `InMemoryOffloadStore`.

    All paths are workspace-relative POSIX-style strings ("a/b.txt"); how
    that maps onto real storage (dict keys, filesystem paths, object keys)
    is entirely up to the implementation.
    """

    @abstractmethod
    async def read_file(self, path: str) -> str:
        """Return the full contents of `path`. Raises FileNotFoundError if
        it doesn't exist."""
        ...

    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        """Create or overwrite `path` with `content`."""
        ...

    @abstractmethod
    async def edit_file(self, path: str, old_text: str, new_text: str) -> None:
        """Replace exactly one occurrence of `old_text` with `new_text` in
        `path`. Raises FileNotFoundError if `path` doesn't exist, or
        ValueError if `old_text` occurs zero times (nothing to replace) or
        more than once (ambiguous — the caller must supply more context)."""
        ...

    @abstractmethod
    async def ls(self, path: str = "") -> list[str]:
        """List direct children of `path` (files and sub-directories,
        directory names suffixed with "/"). Empty/"" lists the root."""
        ...

    @abstractmethod
    async def glob(self, pattern: str) -> list[str]:
        """Return every path matching the shell-style `pattern` (fnmatch
        semantics, `**` supported by implementations that can), sorted."""
        ...

    @abstractmethod
    async def grep(self, pattern: str, path: str | None = None) -> list[GrepMatch]:
        """Regex-search file contents, line by line. `path` scopes the
        search to one file or a directory prefix; omitted searches every
        file in the workspace."""
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """True if `path` refers to an existing file."""
        ...

from __future__ import annotations

import fnmatch
import re

from app.agent_loop_lib.modules.providers.workspace.base import (
    GrepMatch,
    WorkspaceBackend,
)


def _normalize(path: str) -> str:
    return path.strip("/")


class InMemoryWorkspaceBackend(WorkspaceBackend):
    """Non-persistent default — a flat dict of `path -> content`, same role
    as every other `InMemory*` in this codebase. Directories are implied by
    "/" in path strings, not stored as their own entries."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self._files: dict[str, str] = dict(files or {})

    async def read_file(self, path: str) -> str:
        path = _normalize(path)
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    async def write_file(self, path: str, content: str) -> None:
        self._files[_normalize(path)] = content

    async def edit_file(self, path: str, old_text: str, new_text: str) -> None:
        path = _normalize(path)
        if path not in self._files:
            raise FileNotFoundError(path)
        content = self._files[path]
        count = content.count(old_text)
        if count == 0:
            raise ValueError(f"old_text not found in {path!r}")
        if count > 1:
            raise ValueError(f"old_text occurs {count} times in {path!r}; must be unique")
        self._files[path] = content.replace(old_text, new_text, 1)

    async def ls(self, path: str = "") -> list[str]:
        prefix = _normalize(path)
        prefix = f"{prefix}/" if prefix else ""
        children: set[str] = set()
        for full_path in self._files:
            if prefix and not full_path.startswith(prefix):
                continue
            rest = full_path[len(prefix):]
            if not rest:
                continue
            if "/" in rest:
                children.add(rest.split("/", 1)[0] + "/")
            else:
                children.add(rest)
        return sorted(children)

    async def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in self._files if fnmatch.fnmatch(p, pattern))

    async def grep(self, pattern: str, path: str | None = None) -> list[GrepMatch]:
        regex = re.compile(pattern)
        scope = _normalize(path) if path else None
        matches: list[GrepMatch] = []
        for full_path in sorted(self._files):
            if scope and not (full_path == scope or full_path.startswith(scope + "/")):
                continue
            content = self._files[full_path]
            for i, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append(GrepMatch(path=full_path, line_number=i, line=line))
        return matches

    async def exists(self, path: str) -> bool:
        return _normalize(path) in self._files

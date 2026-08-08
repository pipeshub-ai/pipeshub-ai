from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.agent_loop_lib.modules.providers.workspace.base import (
    GrepMatch,
    WorkspaceBackend,
)


class LocalWorkspaceBackend(WorkspaceBackend):
    """Real-filesystem-backed workspace, rooted at `root`. Every path is
    resolved and checked to stay within `root` — an agent (or a prompt
    injection) cannot `read_file("../../etc/passwd")` its way out.

    Blocking file I/O runs via `asyncio.to_thread`, matching the pattern
    used elsewhere in the harness for the same reason (stdlib I/O here is
    synchronous).
    """

    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        candidate = (self._root / path.strip("/")).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError(f"Path escapes workspace root: {path!r}")
        return candidate

    async def read_file(self, path: str) -> str:
        target = self._resolve(path)

        def _read() -> str:
            if not target.is_file():
                raise FileNotFoundError(path)
            return target.read_text()

        return await asyncio.to_thread(_read)

    async def write_file(self, path: str, content: str) -> None:
        target = self._resolve(path)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

        await asyncio.to_thread(_write)

    async def edit_file(self, path: str, old_text: str, new_text: str) -> None:
        content = await self.read_file(path)
        count = content.count(old_text)
        if count == 0:
            raise ValueError(f"old_text not found in {path!r}")
        if count > 1:
            raise ValueError(f"old_text occurs {count} times in {path!r}; must be unique")
        await self.write_file(path, content.replace(old_text, new_text, 1))

    async def ls(self, path: str = "") -> list[str]:
        target = self._resolve(path) if path else self._root

        def _ls() -> list[str]:
            if not target.is_dir():
                raise FileNotFoundError(path)
            return sorted(
                child.name + "/" if child.is_dir() else child.name
                for child in target.iterdir()
            )

        return await asyncio.to_thread(_ls)

    async def glob(self, pattern: str) -> list[str]:
        def _glob() -> list[str]:
            return sorted(
                str(p.relative_to(self._root))
                for p in self._root.glob(pattern)
                if p.is_file()
            )

        return await asyncio.to_thread(_glob)

    async def grep(self, pattern: str, path: str | None = None) -> list[GrepMatch]:
        regex = re.compile(pattern)
        scope = self._resolve(path) if path else self._root

        def _grep() -> list[GrepMatch]:
            if scope.is_file():
                files = [scope]
            elif scope.is_dir():
                files = sorted(p for p in scope.rglob("*") if p.is_file())
            else:
                raise FileNotFoundError(path)
            matches: list[GrepMatch] = []
            for f in files:
                try:
                    text = f.read_text()
                except (UnicodeDecodeError, OSError):
                    continue
                rel = str(f.relative_to(self._root))
                for i, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        matches.append(GrepMatch(path=rel, line_number=i, line=line))
            return matches

        return await asyncio.to_thread(_grep)

    async def exists(self, path: str) -> bool:
        target = self._resolve(path)
        return await asyncio.to_thread(target.is_file)

"""The Dropbox id of an entry's parent folder, without a metadata call per entry.

A listing gives each entry's path but not its parent's id. Asking Dropbox for the parent of every entry
(`files/get_metadata`) doubled the calls of a sync. The parent is almost always known already: listed earlier in
the same listing (parents come before children, and when they don't, the next page has them), or stored by an
earlier sync at that path in the same record group. Only when neither knows it is Dropbox asked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def parent_path(path_lower: str) -> str | None:
    """"/a/b/c.txt" -> "/a/b"; a top-level entry ("/c.txt") has no parent folder in its namespace."""
    path = (path_lower or "").rstrip("/")
    if path.count("/") <= 1:
        return None
    return path.rsplit("/", 1)[0]


class ParentResolver:
    """Parent ids for the entries of one namespace's listing."""

    def __init__(
        self,
        stored_id_at: Callable[[str], Awaitable[str | None]],
        ask_dropbox: Callable[[str], Awaitable[str | None]],
    ) -> None:
        self._stored_id_at = stored_id_at
        self._ask_dropbox = ask_dropbox
        self._listed: dict[str, str] = {}
        self.asked = 0

    def note_folder(self, path_lower: str, item_id: str) -> None:
        self._listed[path_lower.rstrip("/")] = item_id

    def note_gone(self, path_lower: str) -> None:
        self._listed.pop(path_lower.rstrip("/"), None)

    async def parent_id(self, path_lower: str) -> str | None:
        parent = parent_path(path_lower)
        if parent is None:
            return None
        if parent in self._listed:
            return self._listed[parent]
        stored = await self._stored_id_at(parent)
        if stored:
            self._listed[parent] = stored
            return stored
        self.asked += 1
        found = await self._ask_dropbox(parent)
        if found:
            self._listed[parent] = found
        return found

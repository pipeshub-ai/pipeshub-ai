"""Main's FolderScope (``folder_scope.py`` at e29bf542f), frozen as the reference for literal folder values.

Folder patterns must leave every literal value deciding as it did here. The
compatibility tests compare the live ``FolderScope`` with this copy, so do not
edit it to follow the live one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.connectors.core.registry.filters import SyncFilterKey

if TYPE_CHECKING:
    from app.connectors.core.registry.filters import FilterCollection


def _as_folder(path: str) -> str:
    """``'/a/b'`` -> ``'a/b/'``; ``''`` or ``'/'`` -> ``''`` (the whole store)."""
    cleaned = path.strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def folder_path_problems(sync_values: dict[str, object]) -> list[str]:
    """A folder filter value that would climb out of the bucket, container or share."""
    entry = sync_values.get(SyncFilterKey.FOLDER_PATHS.value)
    if not isinstance(entry, dict):
        return []
    raw = entry.get("value")
    for value in raw if isinstance(raw, list) else [raw]:
        if value is None:
            continue
        if any(part.strip() == ".." for part in str(value).replace("\\", "/").split("/")):
            return [f'Folder "{value}" must be a path from the top of the store, without "..".']
    return []


@dataclass(frozen=True)
class FolderScope:
    """Folder prefixes (each ending in ``/``) and whether they are excluded."""

    folders: tuple[str, ...] = ()
    exclude: bool = False

    @classmethod
    def from_filters(cls, sync_filters: FilterCollection | None) -> FolderScope:
        folder_filter = sync_filters.get(SyncFilterKey.FOLDER_PATHS) if sync_filters else None
        if not folder_filter or folder_filter.is_empty():
            return cls()
        raw = folder_filter.value
        values = raw if isinstance(raw, list) else [raw]
        folders = {_as_folder(str(v)) for v in values if v is not None}
        exclude = folder_filter.operator_value == "not_in"
        if "" in folders:
            # The root folder was named: Include covers everything, Exclude nothing.
            return cls(("",), True) if exclude else cls()
        # A folder inside another listed folder adds nothing.
        kept = tuple(sorted(f for f in folders if not any(f != o and f.startswith(o) for o in folders)))
        return cls(kept, exclude)

    @property
    def is_everything(self) -> bool:
        return not self.folders

    @property
    def list_prefixes(self) -> list[str]:
        """Prefixes to list the store with; ``[""]`` means list all of it.

        Excluded folders are listed and then skipped, since the store APIs can
        only narrow a listing to a prefix, not leave one out.
        """
        if self.is_everything or self.exclude:
            return [""]
        return list(self.folders)

    def includes_file(self, path: str) -> bool:
        """Whether a file (its path within the store) is synced."""
        if self.is_everything:
            return True
        inside = any(path.lstrip("/").startswith(f) for f in self.folders)
        return not inside if self.exclude else inside

    def includes_folder(self, path: str) -> bool:
        """Whether a folder record is kept.

        With Include, the folders above a chosen folder are kept too, so the
        tree still leads to it; with Exclude, an excluded folder and everything
        under it is dropped.
        """
        if self.is_everything:
            return True
        folder = _as_folder(path)
        if self.exclude:
            return not any(folder.startswith(f) for f in self.folders)
        return any(folder.startswith(f) or f.startswith(folder) for f in self.folders)

    def key(self) -> str:
        """A stable string for this scope, to tell whether it has changed."""
        # JSON, not a joined string: folder names may contain any separator.
        return json.dumps({"exclude": self.exclude, "folders": sorted(self.folders)})

    def describe(self) -> str:
        if self.is_everything:
            return "all folders"
        names = ", ".join(f or "/" for f in self.folders)
        return f"all folders except {names}" if self.exclude else f"only {names}"

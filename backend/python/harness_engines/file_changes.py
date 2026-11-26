"""What a completed opencode write says it did to each file, for its ``FileChanged`` (FU2).

opencode reports it in the tool's metadata, on its own event stream and in ACP's ``rawOutput.metadata``: ``write``
says whether the file was there before (``exists``), ``apply_patch`` lists each file with its ``type``. Neither
carries a diff, so without this a new file read as ``modified`` while the workspace diff beside it said ``added``.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Final, Literal

from harness_core.engines.jsonview import as_list, as_object, as_str

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue

FileChange = Literal["added", "modified", "deleted"]
_LISTED: Final[Mapping[str, FileChange]] = {"add": "added", "update": "modified", "move": "modified", "delete": "deleted"}


def listed_changes(metadata: JsonValue) -> dict[str, FileChange]:
    """``metadata.files``: path → change, a moved file under its new path; an entry of an unknown type is left out."""
    found: dict[str, FileChange] = {}
    for entry in (as_object(e) for e in as_list(as_object(metadata).get("files"))):
        path = as_str(entry.get("movePath")) or as_str(entry.get("filePath"))
        change = _LISTED.get(as_str(entry.get("type")))
        if path and change is not None:
            found[path] = change
    return found


def workspace_relative(path: str, workdir: str) -> str:
    """``path`` under ``workdir`` as the workspace names it; any other path as it came, normalized."""
    normal = posixpath.normpath(path) if path else ""
    return normal[len(workdir) + 1 :] if normal.startswith(workdir + "/") else normal


def created(metadata: JsonValue) -> bool:
    """Whether a single-file write says the file did not exist before it."""
    return as_object(metadata).get("exists") is False


__all__ = ["FileChange", "created", "listed_changes", "workspace_relative"]

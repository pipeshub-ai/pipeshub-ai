"""The journal event a workspace commit is recorded by, built in one place for every writer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.workspace.changes import FileChangeKind

WORKSPACE_COMMITTED = "workspace.committed"


def workspace_committed(
    workspace_id: str, revision: int, changed: Iterable[str], kinds: Mapping[str, FileChangeKind]
) -> NewEvent:
    """``changed`` and ``kinds`` are workspace-relative; the event names them from ``/``. A path ``kinds`` does not
    name is journaled without a kind (an older Node's merge says none) and read as modified."""
    paths = list(changed)
    data: dict[str, JsonValue] = {
        "workspaceId": workspace_id,
        "revision": revision,
        "changed": ["/" + path for path in paths],
    }
    named: dict[str, JsonValue] = {"/" + path: kinds[path] for path in paths if path in kinds}
    if named:
        data["kinds"] = named
    return NewEvent(WORKSPACE_COMMITTED, data)


__all__ = ["WORKSPACE_COMMITTED", "workspace_committed"]

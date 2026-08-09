"""IWorkflowStateStore port — durable key/value state behind `ctx.state`.

Scoped to (org_id, workflow_id), NOT to a run: the whole point of `ctx.state`
is carrying a value from one run of a recurring workflow to the next (the
"last issue I saw" cursor pattern). Run-scoped replay state is the execution
journal's job, not this store's.
"""
from __future__ import annotations

from typing import Any, Protocol


class IWorkflowStateStore(Protocol):
    async def get(self, *, org_id: str, workflow_id: str, key: str) -> Any:
        """Return the stored value, or None when the key was never set."""
        ...

    async def set(self, *, org_id: str, workflow_id: str, key: str, value: Any) -> None:
        """Write a JSON-serializable value."""
        ...

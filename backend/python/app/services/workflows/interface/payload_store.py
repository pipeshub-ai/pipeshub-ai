"""`IJournalPayloadStore` -- somewhere to park a journal result too large to
keep in the journal itself.

Separate from `ICodeStore` deliberately: code is authored once and read on
every run, whereas these are write-once, read-only-on-replay, and expire with
their run. Keeping the port narrow also keeps the journal decorator testable
without an object store.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.services.workflows.domain.models import ArtifactRef


class IJournalPayloadStore(Protocol):
    async def put(self, *, run_id: str, step_key: str, payload: bytes) -> "ArtifactRef":
        """Store `payload` and return a reference that `get` can resolve.

        `run_id`/`step_key` are for provenance and naming; the returned ref is
        what gets persisted, so an implementation is free to ignore them.
        """
        ...

    async def get(self, ref: "ArtifactRef") -> bytes | None:
        """Resolve a ref written by `put`. None if it no longer exists.

        Returning None rather than raising lets the journal decide: a missing
        spilled payload during replay is a divergence, not an outage.
        """
        ...

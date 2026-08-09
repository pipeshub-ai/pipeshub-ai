"""IExecutionJournal port — durable replay log for code workflows (D1)."""
from __future__ import annotations

from typing import Protocol

from app.services.workflows.domain.models import JournalEntry


class IExecutionJournal(Protocol):
    async def append(self, entry: JournalEntry) -> None:
        """Append one journal entry. Must be idempotent on (run_id, step_key)."""
        ...

    async def lookup(self, run_id: str, step_key: str) -> JournalEntry | None:
        """Return the entry for (run_id, step_key), or None if not yet recorded."""
        ...

    async def load(self, run_id: str) -> list[JournalEntry]:
        """Return all entries for a run, ordered by seq ascending."""
        ...

    async def touch(self, run_id: str) -> str | None:
        """Restart the retention clock on a run's journal.

        A run parked on `ctx.request_approval` writes nothing while it waits,
        so its journal ages out from its last *append*, not from when it
        suspended. Resuming after that expiry replays from the top of the
        workflow against an empty journal: every completed step looks fresh
        and re-executes, which for anything not marked `side_effect=WRITE`
        means silently doing it twice.

        Called at suspension, and returns the ISO deadline after which the
        journal can no longer be relied on -- or None if there is nothing to
        extend, which itself means the journal is already gone.
        """
        ...

    async def compact(self, run_id: str, upto_seq: int) -> None:
        """Checkpoint-and-truncate for long/forever-running workflows.

        No caller today, and a size-based policy would be wrong: replay resolves
        every step through `lookup(run_id, step_key)` from the top of the
        workflow function, so dropping an entry makes its step look fresh on the
        next resume — a re-executed write, or `ReplayDivergence` if the step is
        declared `side_effect=WRITE`. Truncating is only safe once a resume can
        start from a snapshot instead of from the entry point.
        """
        ...

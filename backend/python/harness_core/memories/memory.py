"""The reference ``MemorySource``: one run's pinned memories and its proposals, in process."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final

from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.memories.types import (
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MemoryProposal,
    MemoryRefused,
    MemoryScope,
    MemoryUnavailable,
    ProposedMemory,
    RecalledMemory,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

MAX_PROPOSALS_PER_RUN = 10
MAX_MEMORY_CHARS: Final = RELEASE_DEFAULTS.memory.max_chars


class InMemoryMemorySource:
    """Refuses what a host refuses: a scope the run was not granted, invalid content, the per-run cap."""

    def __init__(
        self,
        recalled: Iterable[RecalledMemory] = (),
        *,
        write_scopes: Iterable[MemoryScope] = (),
        run_id: str = "run",
    ) -> None:
        self._recalled = list(recalled)
        self._scopes = frozenset(write_scopes)
        self._run_id = run_id
        self.proposed: dict[str, MemoryProposal] = {}
        self.down = False

    def forget(self, memory_id: str) -> None:
        """A memory deleted after admission is no longer served."""
        self._recalled = [m for m in self._recalled if m.memory_id != memory_id]

    async def recall(self) -> Sequence[RecalledMemory]:
        if self.down:
            raise MemoryUnavailable("memory store unavailable")
        return list(self._recalled)

    async def propose(self, proposal: MemoryProposal) -> ProposedMemory:
        if self.down:
            raise MemoryUnavailable("memory store unavailable")
        content = proposal.content.strip()
        if not content or len(content) > MAX_MEMORY_CHARS or proposal.kind not in MEMORY_KINDS or proposal.scope not in MEMORY_SCOPES:
            raise MemoryRefused("MEMORY_INVALID")
        if proposal.scope not in self._scopes:
            raise MemoryRefused("MEMORY_WRITE_NOT_GRANTED")
        replaces = proposal.replaces
        if replaces is not None and self._scope_of(replaces) != proposal.scope:
            raise MemoryRefused("MEMORY_REPLACE_UNKNOWN")
        if replaces is not None and replaces in self.proposed:
            # Its own proposal never became a memory: correcting it withdraws it.
            del self.proposed[replaces]
            replaces = None
        digest = hashlib.sha256(f"{self._run_id}\0{proposal.scope}\0{proposal.kind}\0{content}".encode()).hexdigest()
        memory_id = f"mem_{digest[:40]}"
        if memory_id in self.proposed:
            return ProposedMemory(memory_id, "pending", created=False)
        if len(self.proposed) >= MAX_PROPOSALS_PER_RUN:
            raise MemoryRefused("MEMORY_LIMIT")
        self.proposed[memory_id] = MemoryProposal(content, proposal.kind, proposal.scope, replaces)
        return ProposedMemory(memory_id, "pending", created=True)

    def _scope_of(self, memory_id: str) -> str | None:
        """The scope of a memory this run may replace: one it was given or proposed itself."""
        given = next((m.scope for m in self._recalled if m.memory_id == memory_id), None)
        own = self.proposed.get(memory_id)
        return given if given is not None else own.scope if own is not None else None


__all__ = ["InMemoryMemorySource"]

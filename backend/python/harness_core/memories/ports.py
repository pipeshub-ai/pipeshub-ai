"""The port a run reaches governed memory through (P7.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.memories.types import (
        MemoryProposal,
        ProposedMemory,
        RecalledMemory,
    )


class MemorySource(Protocol):
    """One run's view of memory, bound to that run by the host (its token, its pin).

    ``recall`` answers what the run's admission pinned that is still there, in
    the pinned order; ``propose`` keeps a memory the run's model asked to
    remember until the run ends (idempotent for the same proposal). Raises
    ``MemoryRefused`` for a proposal the host refuses and ``MemoryUnavailable``
    when the host cannot be reached.
    """

    async def recall(self) -> Sequence[RecalledMemory]: ...

    async def propose(self, proposal: MemoryProposal) -> ProposedMemory: ...


__all__ = ["MemorySource"]

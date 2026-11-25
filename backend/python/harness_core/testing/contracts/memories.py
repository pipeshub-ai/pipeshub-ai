"""Contract suite for ``MemorySource`` (P7.3 PY-3).

Subclass :class:`MemorySourceContract` and provide ``memory_world``: a
:class:`MemoryWorld` whose ``source`` builds a run's source with the given
memories pinned (as the host stores them, newest first) and the given write
scopes granted, and whose ``forget`` deletes a memory from the host's store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.memories.types import MemoryProposal, MemoryRefused, RecalledMemory

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    from harness_core.memories.ports import MemorySource
    from harness_core.memories.types import MemoryScope

PREFERENCE = RecalledMemory(
    "mem_pref", "user", "preference", "Prefers answers as short bullet lists.", {"complete": True, "sources": []}
)
FACT = RecalledMemory(
    "mem_fact",
    "session",
    "fact",
    "The quarterly report is due on the 5th.",
    {"complete": True, "sources": [{"kind": "indexed", "recordId": "r1"}]},
)


class MemoryWorld(Protocol):
    def source(
        self, recalled: Sequence[RecalledMemory], write_scopes: Sequence[MemoryScope]
    ) -> Awaitable[MemorySource]: ...

    def forget(self, memory_id: str) -> Awaitable[None]: ...


class MemorySourceContract:
    @pytest.fixture
    def memory_world(self) -> MemoryWorld:
        raise NotImplementedError

    async def test_recall_answers_the_pinned_memories_in_order(self, memory_world: MemoryWorld) -> None:
        source = await memory_world.source([PREFERENCE, FACT], ["user"])
        recalled = await source.recall()
        assert [(m.memory_id, m.scope, m.kind, m.content) for m in recalled] == [
            (m.memory_id, m.scope, m.kind, m.content) for m in (PREFERENCE, FACT)
        ]
        assert [dict(m.label) for m in recalled] == [dict(PREFERENCE.label), dict(FACT.label)]

    async def test_a_memory_deleted_after_admission_is_not_served(self, memory_world: MemoryWorld) -> None:
        source = await memory_world.source([PREFERENCE, FACT], ["user"])
        await memory_world.forget(FACT.memory_id)
        assert [m.memory_id for m in await source.recall()] == [PREFERENCE.memory_id]

    async def test_nothing_pinned_recalls_nothing(self, memory_world: MemoryWorld) -> None:
        source = await memory_world.source([], ["user"])
        assert list(await source.recall()) == []

    async def test_a_proposal_is_pending_with_a_stable_id(self, memory_world: MemoryWorld) -> None:
        source = await memory_world.source([], ["user"])
        proposal = MemoryProposal("Works in the Berlin office.", "fact", "user")
        first = await source.propose(proposal)
        again = await source.propose(proposal)
        assert first.status == "pending" and first.created
        assert again.memory_id == first.memory_id and not again.created
        other = await source.propose(MemoryProposal("Works in the Paris office.", "fact", "user"))
        assert other.memory_id != first.memory_id

    async def test_a_scope_not_granted_is_refused(self, memory_world: MemoryWorld) -> None:
        source = await memory_world.source([], ["session"])
        with pytest.raises(MemoryRefused) as refused:
            _ = await source.propose(MemoryProposal("x", "fact", "user"))
        assert refused.value.detail == "MEMORY_WRITE_NOT_GRANTED"

    async def test_invalid_content_is_refused(self, memory_world: MemoryWorld) -> None:
        source = await memory_world.source([], ["user"])
        with pytest.raises(MemoryRefused) as refused:
            _ = await source.propose(MemoryProposal("x" * 501, "fact", "user"))
        assert refused.value.detail == "MEMORY_INVALID"

    async def test_the_run_cap_is_refused(self, memory_world: MemoryWorld) -> None:
        source = await memory_world.source([], ["user"])
        for i in range(10):
            _ = await source.propose(MemoryProposal(f"Fact number {i}.", "fact", "user"))
        with pytest.raises(MemoryRefused) as refused:
            _ = await source.propose(MemoryProposal("One too many.", "fact", "user"))
        assert refused.value.detail == "MEMORY_LIMIT"

    async def test_evalfix2_c2_a_proposal_may_replace_a_memory_the_run_was_given(self, memory_world: MemoryWorld) -> None:
        """A correction names the memory it corrects (its id is in the recalled block): it is a new memory, pending
        like any other, that supersedes the old one once the host accepts it."""
        source = await memory_world.source([PREFERENCE, FACT], ["user", "session"])
        proposed = await source.propose(
            MemoryProposal("Prefers answers as numbered lists.", "preference", "user", replaces=PREFERENCE.memory_id)
        )
        assert proposed.status == "pending" and proposed.created
        assert proposed.memory_id != PREFERENCE.memory_id

    async def test_evalfix2_c2_a_replacement_of_its_own_earlier_proposal_is_allowed(self, memory_world: MemoryWorld) -> None:
        source = await memory_world.source([], ["user"])
        first = await source.propose(MemoryProposal("The office is in Porto.", "fact", "user"))
        second = await source.propose(MemoryProposal("The office is in Lisbon.", "fact", "user", replaces=first.memory_id))
        assert second.created and second.memory_id != first.memory_id

    async def test_evalfix2_c2_replacing_a_memory_not_given_or_in_another_scope_is_refused(
        self, memory_world: MemoryWorld
    ) -> None:
        source = await memory_world.source([PREFERENCE, FACT], ["user", "session"])
        for replaces, scope in (("mem_somebody_else", "user"), (FACT.memory_id, "user"), (PREFERENCE.memory_id, "session")):
            with pytest.raises(MemoryRefused) as refused:
                _ = await source.propose(MemoryProposal("A corrected fact.", "fact", scope, replaces=replaces))  # type: ignore[arg-type]
            assert refused.value.detail == "MEMORY_REPLACE_UNKNOWN", (replaces, scope)


__all__ = ["FACT", "PREFERENCE", "MemorySourceContract", "MemoryWorld"]

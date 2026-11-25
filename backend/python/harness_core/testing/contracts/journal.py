"""Contract suite every ``JournalPort`` adapter must pass.

Subclass :class:`JournalContract` in a test module and provide a ``harness``
fixture returning a :class:`JournalHarness` (the adapter under test plus the
Node-side operations the suite needs). The in-memory adapter runs it in
``tests/unit/harness_core/journal``; the Node-backed client runs it in P1a S5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS
from harness_core.journal.types import (
    Cancelled,
    CommandConflict,
    CommitRejected,
    CommitRequest,
    Complete,
    EventRejected,
    Fail,
    NewEvent,
    OwnershipLost,
    RunNotClaimable,
    Suspend,
    WriterNotAllowed,
    Yield,
)
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.journal.ports import JournalPort
    from harness_core.journal.types import (
        AttemptToken,
        Claim,
        CommitReceipt,
        InteractionDecision,
        JournalEvent,
        Transition,
    )


class JournalHarness(Protocol):
    @property
    def journal(self) -> JournalPort: ...

    @property
    def lease_s(self) -> float: ...

    def admit(
        self,
        run_id: str,
        *,
        session_id: str,
        input_text: str = "hello",
        stream_id: str | None = None,
    ) -> None: ...

    def advance(self, seconds: float) -> None:
        """Move DB time forward."""
        ...

    def sweep(self) -> None:
        """Run one sweeper pass (re-queue expired leases)."""
        ...

    def request_cancel(self, run_id: str) -> None: ...

    def resolve(
        self, run_id: str, interaction_id: str, decision: InteractionDecision
    ) -> None: ...

    def now_iso(self, plus_s: float = 0.0) -> str:
        """DB time as an RFC 3339 ``Z`` timestamp."""
        ...


def turn(n: int) -> NewEvent:
    return NewEvent("turn.started", {"turn": n})


def _types(events: list[JournalEvent]) -> list[str]:
    return [e.type for e in events]


class JournalContract:
    """Behaviour shared by every journal adapter (REPORT 6.1.2-6.1.5, P1a J-1..J-6, J-8)."""

    @pytest.fixture
    def harness(self) -> JournalHarness:
        raise NotImplementedError("provide a harness fixture")

    async def _claim(self, h: JournalHarness, run_id: str) -> Claim:
        claim = await h.journal.claim(run_id, worker_id="w1")
        assert claim is not None
        return claim

    async def _commit(
        self,
        h: JournalHarness,
        token: AttemptToken,
        command_id: str,
        *events: NewEvent,
        transition: Transition | None = None,
    ) -> CommitReceipt:
        return await h.journal.commit(
            CommitRequest(command_id, token, tuple(events), transition)
        )

    async def test_claim_pins_spec_and_returns_the_stream_head(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1", input_text="question")
        claim = await self._claim(harness, "run1")
        assert (claim.attempt, claim.fence) == (1, 1)
        assert claim.spec == minimal_run_spec()
        assert claim.input_text == "question"
        events = await harness.journal.read("ses1")
        assert _types(events) == ["run.admitted", "run.claimed", "run.spec_pinned"]
        assert (
            claim.last_seq == events[-1].seq == await harness.journal.last_seq("ses1")
        )
        assert all(e.writer == "node" for e in events)

    async def test_nothing_claimable_and_running_run_not_claimable(
        self, harness: JournalHarness
    ) -> None:
        assert await harness.journal.claim(worker_id="w1") is None
        harness.admit("run1", session_id="ses1")
        claim = await harness.journal.claim(worker_id="w1")
        assert claim is not None and claim.run_id == "run1"
        with pytest.raises(RunNotClaimable):
            _ = await harness.journal.claim("run1", worker_id="w2")

    async def test_seq_is_gap_free_per_stream_and_streams_are_independent(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        harness.admit("child1", session_id="ses1", stream_id="child1")
        root = await self._claim(harness, "run1")
        child = await self._claim(harness, "child1")
        r1 = await self._commit(harness, root.token, "c1", turn(1), turn(2))
        c1 = await self._commit(harness, child.token, "c2", turn(1))
        r2 = await self._commit(harness, root.token, "c3", turn(3))
        root_seqs = [e.seq for e in await harness.journal.read("ses1")]
        assert root_seqs == list(range(1, len(root_seqs) + 1))
        child_seqs = [e.seq for e in await harness.journal.read("child1")]
        assert child_seqs == list(range(1, len(child_seqs) + 1))
        assert [e.seq for e in r1.events] == [root.last_seq + 1, root.last_seq + 2]
        assert r2.events[0].seq == root.last_seq + 3
        assert c1.events[0].seq == child.last_seq + 1
        after = await harness.journal.read("ses1", after_seq=root.last_seq)
        assert [e.data for e in after] == [{"turn": 1}, {"turn": 2}, {"turn": 3}]

    async def test_committed_events_carry_the_attempt(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        receipt = await self._commit(harness, claim.token, "c1", turn(1))
        (event,) = receipt.events
        assert (event.writer, event.run_id, event.attempt_id, event.fence) == (
            "worker",
            "run1",
            claim.attempt_id,
            claim.fence,
        )
        assert event.ts.endswith("Z")

    async def test_writer_allowlist_rejects_node_events_from_a_worker(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        head = await harness.journal.last_seq("ses1")
        forged = NewEvent("run.completed", {"outcome": "answered"})
        with pytest.raises(WriterNotAllowed):
            _ = await self._commit(harness, claim.token, "c1", turn(1), forged)
        resolved = NewEvent(
            "interaction.resolved",
            {
                "interactionId": "ix1",
                "decision": "approved",
                "by": "p_user",
                "channel": "web",
            },
        )
        with pytest.raises(WriterNotAllowed):
            _ = await self._commit(harness, claim.token, "c2", resolved)
        assert await harness.journal.last_seq("ses1") == head

    async def test_unknown_or_invalid_events_are_rejected_atomically(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        head = await harness.journal.last_seq("ses1")
        with pytest.raises(EventRejected):
            _ = await self._commit(
                harness, claim.token, "c1", turn(1), NewEvent("tool.exploded", {})
            )
        with pytest.raises(EventRejected):
            _ = await self._commit(
                harness,
                claim.token,
                "c2",
                turn(1),
                NewEvent("turn.started", {"turn": "1"}),
            )
        assert await harness.journal.last_seq("ses1") == head

    async def test_stale_fence_is_rejected_after_a_reclaim(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        old = await self._claim(harness, "run1")
        harness.advance(harness.lease_s + 1)
        harness.sweep()
        new = await self._claim(harness, "run1")
        assert (new.attempt, new.fence) == (2, old.fence + 1)
        with pytest.raises(OwnershipLost):
            _ = await self._commit(harness, old.token, "late", turn(1))
        with pytest.raises(OwnershipLost):
            _ = await harness.journal.renew(old.token)
        _ = await self._commit(harness, new.token, "ok", turn(1))

    async def test_expired_lease_rejects_commits_before_any_sweep(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        harness.advance(harness.lease_s + 1)
        with pytest.raises(OwnershipLost):
            _ = await self._commit(harness, claim.token, "c1", turn(1))

    async def test_renew_extends_the_lease(self, harness: JournalHarness) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        harness.advance(harness.lease_s * 0.7)
        grant = await harness.journal.renew(claim.token)
        assert grant.lease_ms == claim.lease_ms
        harness.advance(harness.lease_s * 0.7)
        _ = await self._commit(harness, claim.token, "c1", turn(1))

    async def test_retried_command_returns_the_stored_receipt(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        first = await self._commit(harness, claim.token, "att:turn:1", turn(1))
        again = await self._commit(harness, claim.token, "att:turn:1", turn(1))
        assert again.replayed and not first.replayed
        assert [e.seq for e in again.events] == [e.seq for e in first.events]
        assert _types(await harness.journal.read("ses1", after_seq=claim.last_seq)) == [
            "turn.started"
        ]
        with pytest.raises(CommandConflict):
            _ = await self._commit(harness, claim.token, "att:turn:1", turn(2))

    async def test_perfa_2_a_commit_holds_the_limit_and_one_event_more_is_refused_for_good(
        self, harness: JournalHarness
    ) -> None:
        """JRN-29 (QA22-F1): the limit is the contract's; a larger commit is a permanent refusal that
        writes nothing, and a terminal commit of exactly the limit is accepted."""

        def saved(count: int) -> list[NewEvent]:
            return [NewEvent("loop.state_saved", {"slots": {"n": n}}) for n in range(count)]

        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        full = await self._commit(harness, claim.token, "c1", *saved(MAX_COMMIT_EVENTS))
        assert len(full.events) == MAX_COMMIT_EVENTS
        head = await harness.journal.last_seq("ses1")
        with pytest.raises(CommitRejected) as refused:
            _ = await self._commit(harness, claim.token, "c2", *saved(MAX_COMMIT_EVENTS + 1))
        assert type(refused.value) is CommitRejected, "not a transient failure, nor a conflict"
        assert await harness.journal.last_seq("ses1") == head
        ended = await self._commit(
            harness, claim.token, "c3", *saved(MAX_COMMIT_EVENTS), transition=Fail("kernel_error")
        )
        assert _types(list(ended.events))[-1] == "run.failed"

    async def test_suspend_releases_until_the_interaction_is_resolved(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        requested = NewEvent(
            "interaction.requested",
            {
                "interactionId": "ix1",
                "kind": "approval",
                "bindingDigest": "sha256:" + "1" * 64,
            },
        )
        receipt = await self._commit(
            harness, claim.token, "c1", requested, transition=Suspend("approval", "ix1")
        )
        assert _types(list(receipt.events)) == [
            "interaction.requested",
            "run.suspended",
        ]
        assert receipt.events[-1].writer == "node"
        with pytest.raises(RunNotClaimable):
            _ = await harness.journal.claim("run1", worker_id="w1")
        with pytest.raises(OwnershipLost):
            _ = await self._commit(harness, claim.token, "c2", turn(1))
        harness.resolve("run1", "ix1", "approved")
        tail = await harness.journal.read("ses1", after_seq=receipt.events[-1].seq)
        assert _types(tail) == ["interaction.resolved", "run.resumed"]
        again = await self._claim(harness, "run1")
        assert again.attempt == 1 and again.fence == claim.fence + 1

    async def test_yield_requeues_with_not_before_and_checkpoint(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        yielded = Yield(harness.now_iso(plus_s=10), "drain", checkpoint_ref="ckpt-1")
        receipt = await self._commit(harness, claim.token, "c1", transition=yielded)
        assert _types(list(receipt.events)) == ["run.yielded"]
        assert await harness.journal.claim(worker_id="w1") is None
        harness.advance(11)
        again = await self._claim(harness, "run1")
        assert again.checkpoint_ref == "ckpt-1"
        assert again.attempt == 1

    async def test_terminal_transitions_end_the_run(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        receipt = await self._commit(
            harness, claim.token, "c1", turn(1), transition=Complete()
        )
        assert _types(list(receipt.events)) == ["turn.started", "run.completed"]
        with pytest.raises(RunNotClaimable):
            _ = await harness.journal.claim("run1", worker_id="w1")
        with pytest.raises(OwnershipLost):
            _ = await self._commit(harness, claim.token, "c2", turn(2))

    async def test_crash_loop_counts_attempts(self, harness: JournalHarness) -> None:
        harness.admit("run1", session_id="ses1")
        attempts: list[int] = []
        for _ in range(4):
            claim = await self._claim(harness, "run1")
            attempts.append(claim.attempt)
            harness.advance(harness.lease_s + 1)
            harness.sweep()
        assert attempts == [1, 2, 3, 4]

    async def test_release_requeues_without_a_transition(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        head = await harness.journal.last_seq("ses1")
        await harness.journal.release(claim.token, reason="fold_incompatible")
        assert await harness.journal.last_seq("ses1") == head
        again = await self._claim(harness, "run1")
        assert again.fence == claim.fence + 1

    async def test_cancel_request_is_journaled_for_the_running_worker(
        self, harness: JournalHarness
    ) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        harness.request_cancel("run1")
        tail = await harness.journal.read("ses1", after_seq=claim.last_seq)
        assert _types(tail) == ["run.cancel_requested"]
        receipt = await self._commit(harness, claim.token, "c1", transition=Cancelled())
        assert _types(list(receipt.events)) == ["run.cancelled"]

    async def test_worker_payloads_round_trip(self, harness: JournalHarness) -> None:
        harness.admit("run1", session_id="ses1")
        claim = await self._claim(harness, "run1")
        data: dict[str, JsonValue] = {
            "invocationId": "t1.0",
            # A call that failed before it ran: an ok outcome would need its intent first (H5).
            "status": "error",
            "labels": {"integrity": "trusted"},
            "receipt": {"id": "PROJ-1", "nested": [1, 2.5, None, True]},
        }
        _ = await self._commit(
            harness, claim.token, "c1", NewEvent("tool.outcome", data)
        )
        (event,) = await harness.journal.read("ses1", after_seq=claim.last_seq)
        assert event.data == data


__all__ = ["JournalContract", "JournalHarness", "turn"]

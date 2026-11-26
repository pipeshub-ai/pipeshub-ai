"""Contract suite every ``ChildRunPort`` adapter must pass (P6.1, CRC).

Subclass :class:`ChildRunContract` and provide a ``harness`` fixture returning a
:class:`ChildRunHarness`: the journal and child port under test, and a way to
admit a parent run whose pinned spec grants ``max_children``. The in-memory
journal runs it in ``tests/unit/harness_core/journal``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.journal.children import (
    ChildGrantRequest,
    ChildRefusal,
    ChildRefused,
    ChildRequest,
    JoinReady,
)
from harness_core.journal.inline import INLINE_CAPACITY_BYTES, inline_bytes, inline_ref
from harness_core.journal.types import (
    AttemptToken,
    ChildResultRef,
    CommandConflict,
    CommitRequest,
    Complete,
    OwnershipLost,
    Suspend,
)

if TYPE_CHECKING:
    from harness_core.journal.ports import ChildRunPort, JournalPort
    from harness_core.journal.types import Claim, JournalEvent


class ChildRunHarness(Protocol):
    @property
    def journal(self) -> JournalPort: ...

    @property
    def children(self) -> ChildRunPort: ...

    @property
    def org_id(self) -> str: ...

    def admit_parent(self, run_id: str, *, session_id: str, max_children: int) -> None: ...


RESULT = ChildResultRef(
    result_ref=inline_ref(b'{"output":"child answer","success":true}'),
    labels={"integrity": "internal"},
)


def _request(invocation: str = "t1.0", goal: str = "find it", **grants: object) -> ChildRequest:
    return ChildRequest(
        invocation_id=invocation,
        goal=goal,
        grants=ChildGrantRequest(**grants),  # pyright: ignore[reportArgumentType]
    )


class ChildRunContract:
    @pytest.fixture
    def harness(self) -> ChildRunHarness:
        raise NotImplementedError

    async def _parent(
        self, harness: ChildRunHarness, run_id: str, max_children: int = 2
    ) -> Claim:
        harness.admit_parent(run_id, session_id=f"ses-{run_id}", max_children=max_children)
        claim = await harness.journal.claim(run_id, worker_id="w-parent", org_id=harness.org_id)
        assert claim is not None
        return claim

    async def _types(self, harness: ChildRunHarness, stream_id: str) -> list[str]:
        return [e.type for e in await harness.journal.read(stream_id)]

    async def _events(self, harness: ChildRunHarness, stream_id: str) -> list[JournalEvent]:
        return await harness.journal.read(stream_id)

    async def test_crc1_admits_a_child_on_its_own_stream(self, harness: ChildRunHarness) -> None:
        parent = await self._parent(harness, "p1")
        admitted = await harness.children.spawn(parent.token, _request())
        assert admitted.created
        spawned = [
            e for e in await self._events(harness, parent.stream_id) if e.type == "child.spawned"
        ]
        assert len(spawned) == 1
        assert spawned[0].run_id == "p1"
        assert spawned[0].data["childRunId"] == admitted.child_run_id
        assert spawned[0].data["invocationId"] == "t1.0"
        child = await harness.journal.claim(
            admitted.child_run_id, worker_id="w-child", org_id=harness.org_id
        )
        assert child is not None
        assert child.stream_id == admitted.child_run_id
        assert child.spec.run_kind == "child"
        assert child.input_text == "find it"

    async def test_crc2_the_same_invocation_replays_and_another_request_conflicts(
        self, harness: ChildRunHarness
    ) -> None:
        parent = await self._parent(harness, "p2")
        first = await harness.children.spawn(parent.token, _request())
        before = await self._types(harness, parent.stream_id)
        replay = await harness.children.spawn(parent.token, _request())
        assert (replay.child_run_id, replay.created) == (first.child_run_id, False)
        assert await self._types(harness, parent.stream_id) == before
        with pytest.raises(CommandConflict):
            _ = await harness.children.spawn(parent.token, _request(goal="something else"))

    async def test_crc3_refusals(self, harness: ChildRunHarness) -> None:
        none = await self._parent(harness, "p3", max_children=0)
        with pytest.raises(ChildRefused) as refused:
            _ = await harness.children.spawn(none.token, _request())
        assert refused.value.reason is ChildRefusal.NOT_GRANTED
        one = await self._parent(harness, "p4", max_children=1)
        _ = await harness.children.spawn(one.token, _request("t1.0"))
        with pytest.raises(ChildRefused) as limit:
            _ = await harness.children.spawn(one.token, _request("t1.1"))
        assert limit.value.reason is ChildRefusal.LIMIT_REACHED
        with pytest.raises(ChildRefused) as wide:
            _ = await harness.children.spawn(
                one.token, _request("t1.2", tools=("nobody@x/tool",))
            )
        assert wide.value.reason is ChildRefusal.GRANT_TOO_LARGE
        assert "child.spawned" in await self._types(harness, one.stream_id)
        assert (await self._types(harness, one.stream_id)).count("child.spawned") == 1

    async def test_crc4_grant_too_large_writes_nothing(self, harness: ChildRunHarness) -> None:
        parent = await self._parent(harness, "p5")
        with pytest.raises(ChildRefused) as wide:
            _ = await harness.children.spawn(parent.token, _request(sandbox_profile="agent-code"))
        assert wide.value.reason is ChildRefusal.GRANT_TOO_LARGE
        assert "child.spawned" not in await self._types(harness, parent.stream_id)

    async def test_crc5_a_stale_attempt_admits_nothing(self, harness: ChildRunHarness) -> None:
        parent = await self._parent(harness, "p6")
        stale = AttemptToken(parent.run_id, parent.attempt_id, parent.fence - 1)
        with pytest.raises(OwnershipLost):
            _ = await harness.children.spawn(stale, _request())
        assert "child.spawned" not in await self._types(harness, parent.stream_id)

    async def test_crc6_the_last_child_wakes_a_joining_parent(
        self, harness: ChildRunHarness
    ) -> None:
        parent = await self._parent(harness, "p7")
        admitted = await harness.children.spawn(parent.token, _request())
        _ = await harness.journal.commit(
            CommitRequest(f"{parent.attempt_id}:suspend:1", parent.token, (), Suspend("join"))
        )
        child = await harness.journal.claim(
            admitted.child_run_id, worker_id="w-child", org_id=harness.org_id
        )
        assert child is not None
        _ = await harness.journal.commit(
            CommitRequest(
                f"{child.attempt_id}:complete:1",
                child.token,
                (),
                Complete("answered", child_result=RESULT),
            )
        )
        tail = (await self._events(harness, parent.stream_id))[-2:]
        assert [e.type for e in tail] == ["child.completed", "run.resumed"]
        assert tail[0].data["childRunId"] == admitted.child_run_id
        assert tail[0].data["outcome"] == "answered"
        assert tail[1].data == {"cause": "child_done"}
        again = await harness.journal.claim("p7", worker_id="w-parent", org_id=harness.org_id)
        assert again is not None and again.fence == parent.fence + 1

    async def test_crc7_join_with_every_child_ended_is_refused(
        self, harness: ChildRunHarness
    ) -> None:
        parent = await self._parent(harness, "p8")
        admitted = await harness.children.spawn(parent.token, _request())
        child = await harness.journal.claim(
            admitted.child_run_id, worker_id="w-child", org_id=harness.org_id
        )
        assert child is not None
        _ = await harness.journal.commit(
            CommitRequest(
                f"{child.attempt_id}:complete:1",
                child.token,
                (),
                Complete("answered", child_result=RESULT),
            )
        )
        with pytest.raises(JoinReady):
            _ = await harness.journal.commit(
                CommitRequest(f"{parent.attempt_id}:suspend:1", parent.token, (), Suspend("join"))
            )
        assert "run.suspended" not in await self._types(harness, parent.stream_id)

    async def test_crc8_a_child_completes_with_the_largest_result_a_ref_carries_inline(
        self, harness: ChildRunHarness
    ) -> None:
        """CHILDREF: a child's result is a ``ContentRef`` like any other. The commit takes the longest inline
        ref the contract allows, and the parent's ``child.completed`` carries it whole. Node refused any over 4,096
        characters, so every answer over about 3 KB failed and was retried until quarantined."""
        parent = await self._parent(harness, "p9")
        admitted = await harness.children.spawn(parent.token, _request())
        child = await harness.journal.claim(
            admitted.child_run_id, worker_id="w-child", org_id=harness.org_id
        )
        assert child is not None
        document = (b'{"output":"' + b"x" * (INLINE_CAPACITY_BYTES - 32) + b'","success":true}').ljust(
            INLINE_CAPACITY_BYTES
        )
        large = ChildResultRef(result_ref=inline_ref(document), labels={"integrity": "internal"})
        _ = await harness.journal.commit(
            CommitRequest(f"{child.attempt_id}:complete:1", child.token, (), Complete("answered", child_result=large))
        )
        (completed,) = [e for e in await self._events(harness, parent.stream_id) if e.type == "child.completed"]
        assert completed.data["outcome"] == "answered"
        carried = completed.data["resultRef"]
        assert isinstance(carried, dict)
        assert carried["ref"] == large.result_ref.ref
        assert inline_bytes(large.result_ref) == document


__all__ = ["ChildRunContract", "ChildRunHarness"]

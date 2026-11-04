import asyncio

from app.services.model_identity import metrics
from app.services.model_identity.groups import (
    GroupMembership,
    GroupsHandle,
    NoopGroupResolver,
)


class _Resolver:
    def __init__(self, ids=("g1", "g2"), delay=0.0, error=None) -> None:
        self.ids, self.delay, self.error = ids, delay, error
        self.calls = 0

    async def resolve(self, org_id, subject) -> GroupMembership:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return GroupMembership(ids=tuple(self.ids), source="test")


async def test_noop_resolver_knows_nothing(alice) -> None:
    result = await NoopGroupResolver().resolve("org-1", alice)
    assert result.ids == () and result.complete


async def test_wait_returns_the_resolved_groups(alice) -> None:
    handle = GroupsHandle(_Resolver(), alice)
    result = await handle.wait()
    assert result.ids == ("g1", "g2") and result.complete and result.source == "test"


async def test_many_waiters_share_one_lookup(alice) -> None:
    resolver = _Resolver(delay=0.01)
    handle = GroupsHandle(resolver, alice)
    handle.prefetch()
    results = await asyncio.gather(*(handle.wait(1.0) for _ in range(10)))
    assert resolver.calls == 1
    assert all(r.ids == ("g1", "g2") for r in results)


async def test_prefetch_lets_the_lookup_run_while_the_caller_does_other_work(alice) -> None:
    resolver = _Resolver(delay=0.02)
    handle = GroupsHandle(resolver, alice)
    handle.prefetch()
    assert handle.result_nowait() is None
    await asyncio.sleep(0.05)
    assert handle.result_nowait().ids == ("g1", "g2")


async def test_a_slow_resolver_gives_an_incomplete_result_not_an_error(alice) -> None:
    handle = GroupsHandle(_Resolver(delay=1.0), alice)
    result = await handle.wait(0.01)
    assert result.ids == () and not result.complete and result.source == "timeout"
    assert metrics.counter_value(metrics.IDENTITY_MISSING, "groups_timeout") == 1


async def test_timeout_does_not_cancel_the_shared_lookup(alice) -> None:
    handle = GroupsHandle(_Resolver(delay=0.05), alice)
    await handle.wait(0.001)
    await asyncio.sleep(0.1)
    assert handle.result_nowait().ids == ("g1", "g2")


async def test_a_failing_resolver_degrades(alice) -> None:
    handle = GroupsHandle(_Resolver(error=RuntimeError("boom")), alice)
    result = await handle.wait()
    assert not result.complete and result.source == "error"
    assert handle.result_nowait().source == "error"


def test_result_nowait_without_a_started_lookup_is_none(alice) -> None:
    assert GroupsHandle(_Resolver(), alice).result_nowait() is None


def test_prefetch_without_a_running_loop_is_a_no_op(alice) -> None:
    handle = GroupsHandle(_Resolver(), alice)
    handle.prefetch()
    assert handle.result_nowait() is None


async def test_resolved_handle_answers_immediately() -> None:
    membership = GroupMembership(ids=("a",), source="cache")
    handle = GroupsHandle.resolved(membership)
    assert await handle.wait() is membership
    assert handle.result_nowait() is membership

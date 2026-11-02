"""Tests for SandboxManager — concurrency limits, governor integration,
idempotent teardown, and the register_backend (factory) path."""

from __future__ import annotations

import asyncio

import pytest

from app.agent_loop_lib.sandbox.coding.base import SandboxContext
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxResourceGovernor,
)
from app.agent_loop_lib.sandbox.governor import (
    SandboxLimitExceeded as GovernorLimitExceeded,
)
from app.agent_loop_lib.sandbox.manager import (
    SandboxLimitExceeded,
    SandboxLimits,
    SandboxManager,
    SandboxManagerError,
    SandboxType,
    UnknownSandboxError,
)


class _FakeBackend:
    """Provision yields to the event loop before completing.

    A fake whose `provision()` never suspends makes every concurrency test
    here vacuous: the manager's reserve-then-provision window only opens
    across an await, so with an instant provision the checks pass no matter
    how the limit is implemented. Real backends all provision over I/O.
    """

    def __init__(self):
        self.sandbox_id = f"fake-{id(self)}"
        self._provisioned = False
        self._destroyed = False

    async def provision(self):
        await asyncio.sleep(0.01)
        self._provisioned = True
        return None

    async def destroy(self):
        self._destroyed = True


class _FakeFactory:
    def create(self, ctx):
        return _FakeBackend()


def _make_manager(
    *,
    limits: SandboxLimits | None = None,
    governor: SandboxResourceGovernor | None = None,
) -> SandboxManager:
    mgr = SandboxManager(governor=governor)
    mgr.register_backend_factory(
        SandboxType.CODING,
        _FakeBackend,
        limits=limits or SandboxLimits(),
    )
    return mgr


class TestSandboxManagerConcurrency:
    async def test_get_or_create_creates_new(self) -> None:
        mgr = _make_manager()
        sid, backend = await mgr.get_or_create(SandboxType.CODING)
        assert sid is not None
        assert isinstance(backend, _FakeBackend)
        assert backend._provisioned

    async def test_get_or_create_reuses_existing(self) -> None:
        mgr = _make_manager()
        sid1, backend1 = await mgr.get_or_create(SandboxType.CODING)
        sid2, backend2 = await mgr.get_or_create(SandboxType.CODING, sandbox_id=sid1)
        assert sid1 == sid2
        assert backend1 is backend2

    async def test_get_or_create_unknown_id_raises(self) -> None:
        mgr = _make_manager()
        with pytest.raises(UnknownSandboxError):
            await mgr.get_or_create(SandboxType.CODING, sandbox_id="does-not-exist")

    async def test_max_concurrent_enforced(self) -> None:
        mgr = _make_manager(limits=SandboxLimits(max_concurrent=2))
        await mgr.get_or_create(SandboxType.CODING)
        await mgr.get_or_create(SandboxType.CODING)
        with pytest.raises(SandboxLimitExceeded):
            await mgr.get_or_create(SandboxType.CODING)

    async def test_destroy_releases_slot(self) -> None:
        mgr = _make_manager(limits=SandboxLimits(max_concurrent=2))
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        await mgr.get_or_create(SandboxType.CODING)
        await mgr.destroy(SandboxType.CODING, sid1)
        sid3, _ = await mgr.get_or_create(SandboxType.CODING)
        assert sid3 is not None

    async def test_destroy_is_idempotent(self) -> None:
        mgr = _make_manager()
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        await mgr.destroy(SandboxType.CODING, sid)
        await mgr.destroy(SandboxType.CODING, sid)

    async def test_destroy_all(self) -> None:
        mgr = _make_manager()
        for _ in range(3):
            await mgr.get_or_create(SandboxType.CODING)
        assert mgr.active_count(SandboxType.CODING) == 3
        await mgr.destroy_all()
        assert mgr.active_count(SandboxType.CODING) == 0

    async def test_provision_timeout(self) -> None:
        class _SlowBackend(_FakeBackend):
            async def provision(self):
                await asyncio.sleep(5)

        mgr = SandboxManager()
        mgr.register_backend_factory(
            SandboxType.CODING,
            _SlowBackend,
            limits=SandboxLimits(provision_timeout_s=0.01),
        )
        with pytest.raises((SandboxManagerError, asyncio.TimeoutError)):
            await mgr.get_or_create(SandboxType.CODING)

    async def test_governor_integration(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        mgr = _make_manager(governor=gov)
        await mgr.get_or_create(SandboxType.CODING)
        with pytest.raises(GovernorLimitExceeded):
            await mgr.get_or_create(SandboxType.CODING)

    async def test_governor_release_on_destroy(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=5))
        mgr = _make_manager(governor=gov)
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        assert gov.snapshot()["total"] == 1
        await mgr.destroy(SandboxType.CODING, sid)
        assert gov.snapshot()["total"] == 0

    async def test_concurrent_creates_respect_lock(self) -> None:
        mgr = _make_manager(limits=SandboxLimits(max_concurrent=5))
        results = await asyncio.gather(
            *[mgr.get_or_create(SandboxType.CODING) for _ in range(20)],
            return_exceptions=True,
        )
        created = [r for r in results if not isinstance(r, BaseException)]
        failed = [r for r in results if isinstance(r, SandboxLimitExceeded)]
        assert len(created) == 5
        assert len(failed) == 15

    async def test_refs_skips_backends_without_a_ref(self) -> None:
        """A `SandboxRef` naming no registered factory can never be
        reconnected, so a backend that doesn't publish one is omitted
        rather than given a synthesised ref."""
        mgr = _make_manager()
        await mgr.get_or_create(SandboxType.CODING)
        assert mgr.refs() == []

    async def test_refs_returns_sandbox_refs(self) -> None:
        from app.agent_loop_lib.sandbox.coding.base import SandboxRef

        class _RefBackend(_FakeBackend):
            @property
            def ref(self) -> SandboxRef:
                return SandboxRef(
                    backend="local", sandbox_id=self.sandbox_id, created_at=1.0,
                )

        mgr = SandboxManager()
        mgr.register_backend_factory(SandboxType.CODING, _RefBackend)
        await mgr.get_or_create(SandboxType.CODING)
        await mgr.get_or_create(SandboxType.CODING)

        refs = mgr.refs()
        assert len(refs) == 2
        assert all(isinstance(r, SandboxRef) for r in refs)
        # The backend field must be the registry key, not a class name —
        # it is what `registry.get(...)` is handed on reconnect.
        assert {r.backend for r in refs} == {"local"}
        assert len({r.sandbox_id for r in refs}) == 2

    async def test_register_backend_with_factory(self) -> None:
        mgr = SandboxManager()
        mgr.register_backend(
            SandboxType.CODING,
            _FakeFactory(),
            ctx=SandboxContext(),
        )
        sid, backend = await mgr.get_or_create(SandboxType.CODING)
        assert sid is not None
        assert isinstance(backend, _FakeBackend)
        assert backend._provisioned


class _PerCallBackend(_FakeBackend):
    """Not metered, like Docker: it only uses resources while a call runs."""

    class capabilities:  # stands in for SandboxCapabilities
        is_metered = False


class TestPerCallSlots:
    def _manager(self, gov: SandboxResourceGovernor, wait_s: float = 0.0) -> SandboxManager:
        mgr = SandboxManager(governor=gov, slot_wait_s=wait_s)
        mgr.register_backend_factory(SandboxType.CODING, _PerCallBackend)
        return mgr

    async def test_idle_sandbox_holds_no_slot(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        mgr = self._manager(gov)
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        await mgr.get_or_create(SandboxType.CODING)  # a second idle sandbox still fits
        assert gov.snapshot()["total"] == 0

        async with mgr.slot(SandboxType.CODING, sid):
            assert gov.snapshot()["total"] == 1
        assert gov.snapshot()["total"] == 0

    async def test_call_waits_for_a_free_slot(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        mgr = self._manager(gov, wait_s=5)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        release = asyncio.Event()

        async def first() -> None:
            async with mgr.slot(SandboxType.CODING, sid1):
                await release.wait()

        async def second() -> str:
            async with mgr.slot(SandboxType.CODING, sid2):
                return "ran"

        t1 = asyncio.create_task(first())
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(second())
        await asyncio.sleep(0.02)
        assert not t2.done()
        release.set()
        assert await asyncio.wait_for(t2, 1) == "ran"
        await t1
        assert gov.snapshot()["total"] == 0

    async def test_full_without_wait_raises(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        mgr = self._manager(gov)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        async with mgr.slot(SandboxType.CODING, sid1):
            with pytest.raises(GovernorLimitExceeded):
                async with mgr.slot(SandboxType.CODING, sid2):
                    pass

    async def test_the_wait_note_names_the_tool_and_is_cleared_once_the_slot_is_granted(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        notes: list[tuple[str, bool]] = []

        async def note(tool: str, waiting: bool) -> None:
            notes.append((tool, waiting))

        mgr = SandboxManager(governor=gov, slot_wait_s=5, on_slot_wait=note)
        mgr.register_backend_factory(SandboxType.CODING, _PerCallBackend)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        async with mgr.slot(SandboxType.CODING, sid1):
            pass
        assert notes == []  # no wait, no note
        release = asyncio.Event()

        async def first() -> None:
            async with mgr.slot(SandboxType.CODING, sid1):
                await release.wait()

        async def second() -> list[tuple[str, bool]]:
            async with mgr.slot(SandboxType.CODING, sid2, tool="install_packages"):
                return list(notes)

        t1 = asyncio.create_task(first())
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(second())
        await asyncio.sleep(0.02)
        assert notes == [("install_packages", True)]
        release.set()
        assert await asyncio.wait_for(t2, 1) == [("install_packages", True), ("install_packages", False)]
        await t1

    async def test_a_call_cancelled_while_waiting_takes_no_slot(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        mgr = self._manager(gov, wait_s=5)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        async with mgr.slot(SandboxType.CODING, sid1):
            waiter = asyncio.create_task(mgr.slot(SandboxType.CODING, sid2).__aenter__())
            await asyncio.sleep(0.02)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert gov.snapshot()["total"] == 1
        assert gov.snapshot()["total"] == 0
        async with mgr.slot(SandboxType.CODING, sid2):  # the slot is free again
            assert gov.snapshot()["total"] == 1

    async def test_a_failing_wait_note_does_not_fail_the_call(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))

        async def broken(tool: str, waiting: bool) -> None:
            raise RuntimeError("stream closed")

        mgr = SandboxManager(governor=gov, slot_wait_s=5, on_slot_wait=broken)
        mgr.register_backend_factory(SandboxType.CODING, _PerCallBackend)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        release = asyncio.Event()

        async def first() -> None:
            async with mgr.slot(SandboxType.CODING, sid1):
                await release.wait()

        async def second() -> str:
            async with mgr.slot(SandboxType.CODING, sid2):
                return "ran"

        t1 = asyncio.create_task(first())
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(second())
        await asyncio.sleep(0.02)
        release.set()
        assert await asyncio.wait_for(t2, 1) == "ran"
        await t1

    async def test_the_wait_note_is_cleared_when_the_wait_times_out(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        notes: list[tuple[str, bool]] = []

        async def note(tool: str, waiting: bool) -> None:
            notes.append((tool, waiting))

        mgr = SandboxManager(governor=gov, slot_wait_s=0.05, on_slot_wait=note)
        mgr.register_backend_factory(SandboxType.CODING, _PerCallBackend)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        async with mgr.slot(SandboxType.CODING, sid1):
            with pytest.raises(GovernorLimitExceeded):
                async with mgr.slot(SandboxType.CODING, sid2):
                    pass
        assert notes == [("run_code", True), ("run_code", False)]


class TestMeteredSlots:
    """Metered backends (the default when a backend reports no capabilities) keep their slot for life."""

    async def test_calls_on_a_metered_sandbox_take_no_extra_slot(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        mgr = SandboxManager(governor=gov)
        mgr.register_backend_factory(SandboxType.CODING, _FakeBackend)
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        assert gov.snapshot()["total"] == 1
        async with mgr.slot(SandboxType.CODING, sid):
            assert gov.snapshot()["total"] == 1
        await mgr.destroy_all()
        assert gov.snapshot()["total"] == 0

    async def test_a_creation_waiting_for_a_slot_does_not_hold_the_manager_lock(self) -> None:
        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        holder = SandboxManager(governor=gov)
        holder.register_backend_factory(SandboxType.CODING, _FakeBackend)
        await holder.get_or_create(SandboxType.CODING)  # takes the only slot
        mgr = SandboxManager(governor=gov, slot_wait_s=5)
        mgr.register_backend_factory(SandboxType.CODING, _FakeBackend)
        waiting = asyncio.create_task(mgr.get_or_create(SandboxType.CODING))
        await asyncio.sleep(0.02)
        assert not waiting.done()
        assert not mgr._lock.locked()
        await holder.destroy_all()
        await asyncio.wait_for(waiting, 1)
        await mgr.destroy_all()
        assert gov.snapshot()["total"] == 0

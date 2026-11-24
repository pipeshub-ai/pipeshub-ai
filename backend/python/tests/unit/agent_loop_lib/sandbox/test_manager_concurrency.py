"""Tests for SandboxManager — concurrency limits, governor integration,
idempotent teardown, and the register_backend (factory) path."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


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
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        mgr = _make_manager(governor=gov)
        await mgr.get_or_create(SandboxType.CODING)
        with pytest.raises(GovernorLimitExceeded):
            await mgr.get_or_create(SandboxType.CODING)

    async def test_governor_release_on_destroy(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=5))
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
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        mgr = self._manager(gov)
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        await mgr.get_or_create(SandboxType.CODING)  # a second idle sandbox still fits
        assert gov.snapshot()["total"] == 0

        async with mgr.slot(SandboxType.CODING, sid):
            assert gov.snapshot()["total"] == 1
        assert gov.snapshot()["total"] == 0

    async def test_call_waits_for_a_free_slot(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
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
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        mgr = self._manager(gov)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        async with mgr.slot(SandboxType.CODING, sid1):
            with pytest.raises(GovernorLimitExceeded):
                async with mgr.slot(SandboxType.CODING, sid2):
                    pass

    async def test_the_wait_note_names_the_tool_and_is_cleared_once_the_slot_is_granted(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
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
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
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

    async def test_a_stalled_wait_note_does_not_stretch_the_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.agent_loop_lib.sandbox.manager as manager_mod

        monkeypatch.setattr(manager_mod, "_SLOT_NOTE_S", 0.05)
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))

        async def stalled(tool: str, waiting: bool) -> None:
            await asyncio.Event().wait()  # an event stream nobody reads

        mgr = SandboxManager(governor=gov, slot_wait_s=0.1, on_slot_wait=stalled)
        mgr.register_backend_factory(SandboxType.CODING, _PerCallBackend)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        async with mgr.slot(SandboxType.CODING, sid1):
            with pytest.raises(GovernorLimitExceeded):
                await asyncio.wait_for(mgr.slot(SandboxType.CODING, sid2).__aenter__(), 2)
        assert gov.snapshot()["total"] == 0

    async def test_a_failing_wait_note_does_not_fail_the_call(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))

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
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
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


class TestSlotOutlivesTheCompute:
    async def test_next_waiter_is_granted_only_after_the_cancelled_calls_cleanup(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        mgr = SandboxManager(governor=gov, slot_wait_s=5)
        mgr.register_backend_factory(SandboxType.CODING, _PerCallBackend)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        events: list[str] = []
        running = asyncio.Event()

        async def first() -> None:
            async with mgr.slot(SandboxType.CODING, sid1):
                running.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    # A backend stopping its container before it lets the cancel through.
                    await asyncio.shield(asyncio.sleep(0.2))
                    events.append("cleanup done")
                    raise

        async def second() -> None:
            async with mgr.slot(SandboxType.CODING, sid2):
                events.append("second granted")

        t1 = asyncio.create_task(first())
        await running.wait()
        t2 = asyncio.create_task(second())
        await asyncio.sleep(0.02)
        t1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t1
        await asyncio.wait_for(t2, 1)
        assert events == ["cleanup done", "second granted"]
        assert gov.snapshot()["total"] == 0


class TestMeteredSlots:
    """Metered backends (the default when a backend reports no capabilities) keep their slot for life."""

    async def test_calls_on_a_metered_sandbox_take_no_extra_slot(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        mgr = SandboxManager(governor=gov)
        mgr.register_backend_factory(SandboxType.CODING, _FakeBackend)
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        assert gov.snapshot()["total"] == 1
        async with mgr.slot(SandboxType.CODING, sid):
            assert gov.snapshot()["total"] == 1
        await mgr.destroy_all()
        assert gov.snapshot()["total"] == 0

    async def test_a_creation_waiting_for_a_slot_does_not_hold_the_manager_lock(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
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


class TestCancelSafety:
    """Stop cancels inside the slot and provisioning code routinely, so a
    cancel at any await there must not leak a slot or a backend."""

    def _manager(self, gov: SandboxResourceGovernor, **kwargs) -> SandboxManager:
        mgr = SandboxManager(governor=gov, slot_wait_s=5, **kwargs)
        mgr.register_backend_factory(SandboxType.CODING, _PerCallBackend)
        return mgr

    async def test_stop_during_the_slot_wait_leaves_at_once_and_clears_the_note(self) -> None:
        from app.agent_loop_lib.core.context import CancellationToken
        from app.agent_loop_lib.sandbox.manager import SandboxCallStopped

        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        notes: list[bool] = []

        async def note(tool: str, waiting: bool) -> None:
            notes.append(waiting)

        token = CancellationToken()
        mgr = self._manager(gov, on_slot_wait=note, stop_token=token)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)

        async def wait_for_slot() -> None:
            async with mgr.slot(SandboxType.CODING, sid2):
                raise AssertionError("must not get the slot")

        async with mgr.slot(SandboxType.CODING, sid1):
            task = asyncio.ensure_future(mgr.unless_stopped(wait_for_slot, tool="run_code"))
            await asyncio.sleep(0.02)
            loop = asyncio.get_running_loop()
            stopped_at = loop.time()
            token.cancel()
            with pytest.raises(SandboxCallStopped):
                await task
            assert loop.time() - stopped_at < 0.05
            assert gov.snapshot()["total"] == 1
        assert notes == [True, False]
        assert gov.snapshot()["total"] == 0

    async def test_cancel_while_clearing_the_note_after_a_grant_releases_the_slot(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        clearing = asyncio.Event()

        async def note(tool: str, waiting: bool) -> None:
            if not waiting:
                clearing.set()
                await asyncio.sleep(60)  # a full SSE queue

        mgr = self._manager(gov, on_slot_wait=note)
        sid1, _ = await mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await mgr.get_or_create(SandboxType.CODING)
        first_done = asyncio.Event()

        async def first() -> None:
            async with mgr.slot(SandboxType.CODING, sid1):
                await first_done.wait()

        t1 = asyncio.ensure_future(first())
        await asyncio.sleep(0.01)
        waiter = asyncio.ensure_future(mgr.slot(SandboxType.CODING, sid2).__aenter__())
        await asyncio.sleep(0.02)
        first_done.set()
        await t1
        await asyncio.wait_for(clearing.wait(), 1)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await asyncio.sleep(0.01)
        assert gov.snapshot()["total"] == 0

    async def test_cancel_during_a_contended_release_still_releases(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=2))
        mgr = self._manager(gov)
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        inside = asyncio.Event()

        async def call() -> None:
            async with mgr.slot(SandboxType.CODING, sid):
                inside.set()
                await asyncio.sleep(0.05)

        release = gov.release

        async def slow_release(lease) -> None:
            await asyncio.sleep(0.08)  # the body has returned; the release is in flight
            await release(lease)

        gov.release = slow_release  # type: ignore[method-assign]
        task = asyncio.ensure_future(call())
        await inside.wait()
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)
        assert gov.snapshot()["total"] == 0

    async def test_cancel_during_a_metered_provision_destroys_it_and_frees_the_slot(self) -> None:
        destroyed = 0
        provisioning = asyncio.Event()

        class _SlowMetered(_FakeBackend):
            async def provision(self) -> None:
                provisioning.set()
                await asyncio.sleep(60)

            async def destroy(self) -> None:
                nonlocal destroyed
                destroyed += 1

        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        mgr = SandboxManager(governor=gov)
        mgr.register_backend_factory(SandboxType.CODING, _SlowMetered)
        task = asyncio.ensure_future(mgr.get_or_create(SandboxType.CODING))
        await provisioning.wait()
        assert gov.snapshot()["total"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert destroyed == 1
        assert gov.snapshot()["total"] == 0
        assert mgr._instances == {}

    async def test_a_second_cancel_during_the_destroy_still_frees_the_slot(self) -> None:
        provisioning, destroying = asyncio.Event(), asyncio.Event()

        class _SlowDestroy(_FakeBackend):
            async def provision(self) -> None:
                provisioning.set()
                await asyncio.sleep(60)

            async def destroy(self) -> None:
                destroying.set()
                await asyncio.sleep(0.05)

        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        mgr = SandboxManager(governor=gov)
        mgr.register_backend_factory(SandboxType.CODING, _SlowDestroy)
        task = asyncio.ensure_future(mgr.get_or_create(SandboxType.CODING))
        await provisioning.wait()
        task.cancel()
        await destroying.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)
        assert gov.snapshot()["total"] == 0

    async def test_after_stop_the_next_waiter_is_granted_only_once_cleanup_finished(self) -> None:
        from app.agent_loop_lib.core.context import CancellationToken
        from app.agent_loop_lib.sandbox.manager import SandboxCallStopped

        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        token = CancellationToken()
        stopped_mgr = self._manager(gov, stop_token=token)
        other_mgr = self._manager(gov)
        sid1, _ = await stopped_mgr.get_or_create(SandboxType.CODING)
        sid2, _ = await other_mgr.get_or_create(SandboxType.CODING)
        events: list[str] = []
        running = asyncio.Event()

        async def run() -> None:
            async with stopped_mgr.slot(SandboxType.CODING, sid1):
                running.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.2)  # the container being removed
                    events.append("cleanup done")
                    raise

        async def other() -> None:
            async with other_mgr.slot(SandboxType.CODING, sid2):
                events.append("other granted")

        stopped = asyncio.ensure_future(stopped_mgr.unless_stopped(run, tool="run_code"))
        await running.wait()
        waiter = asyncio.ensure_future(other())
        await asyncio.sleep(0.02)
        token.cancel()
        with pytest.raises(SandboxCallStopped):
            await stopped
        await asyncio.wait_for(waiter, 1)
        assert events == ["cleanup done", "other granted"]


class TestGovernorsSharingSlots:
    """Two governors on one slot directory stand in for two query workers."""

    @pytest.fixture
    def governors(self, tmp_path: Path) -> Iterator[list[SandboxResourceGovernor]]:
        from app.agent_loop_lib.sandbox.capacity import FixedLimit, SandboxCapacity
        from app.services.resource_governor.shared_slots import SharedSlots

        made = []
        for _ in range(2):
            capacity = SandboxCapacity.fixed(1, slot_files=4)
            made.append(SandboxResourceGovernor(
                capacity, SharedSlots(tmp_path, 4), limit_provider=FixedLimit(capacity),
            ))
        yield made
        for gov in made:
            gov.close()

    async def test_a_metered_sandbox_holds_its_slot_for_life_across_workers(self, governors: list[SandboxResourceGovernor]) -> None:
        one, two = (_make_manager(governor=g) for g in governors)
        sid, _ = await one.get_or_create(SandboxType.CODING)
        with pytest.raises(SandboxLimitExceeded):
            await two.get_or_create(SandboxType.CODING)
        await one.destroy(SandboxType.CODING, sid)
        await two.get_or_create(SandboxType.CODING)

    async def test_per_call_slots_are_counted_across_workers(self, governors: list[SandboxResourceGovernor]) -> None:
        one, two = (SandboxManager(governor=g) for g in governors)
        for mgr in (one, two):
            mgr.register_backend_factory(SandboxType.CODING, _PerCallBackend)
        sid_one, _ = await one.get_or_create(SandboxType.CODING)
        sid_two, _ = await two.get_or_create(SandboxType.CODING)
        async with one.slot(SandboxType.CODING, sid_one):
            with pytest.raises(SandboxLimitExceeded):
                async with two.slot(SandboxType.CODING, sid_two):
                    pass
        async with two.slot(SandboxType.CODING, sid_two):
            assert governors[0].snapshot()["total"] == 1

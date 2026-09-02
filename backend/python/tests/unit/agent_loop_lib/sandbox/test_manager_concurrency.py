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

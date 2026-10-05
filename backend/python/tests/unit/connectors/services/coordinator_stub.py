"""A sync coordinator double for the EventService tests.

It admits by default, so tests can assert what happens around admission without
wiring Redis, and its `spawn` behaves like the real one: `_handle_start_sync`
decides whether to release the lease from `lease.task`, not from what `spawn`
returns.
"""

import contextlib
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch


def spawned(lease, coro) -> MagicMock:
    """Started: owns the coroutine and assigns the task to the lease."""
    coro.close()
    lease.task = MagicMock(name="task")
    return lease.task


def declined(_lease, coro) -> None:
    """Already running: None, with lease.task left unset. The coroutine is closed
    so the test does not leave it unawaited."""
    coro.close()


class StubCoordinator:
    def __init__(self) -> None:
        self.acquired: list[str] = []
        self.released: list[str] = []
        self.spawn = AsyncMock(side_effect=spawned)
        self.reports_liveness = False
        # Mocks, not methods: tests set .return_value on these to say whether a
        # sync is already in flight.
        self.is_running_here = MagicMock(return_value=False)
        self.is_running = AsyncMock(return_value=False)
        self.cancel_and_wait = AsyncMock()
        self.request_stop = AsyncMock(return_value=False)
        #: What begin() answers; None means GRANTED.
        self.admission = None

    async def try_claim_org(self, org_id) -> bool:
        return True

    async def begin(self, connector_id, *, org_id=None, message_ts_ms=None) -> tuple:
        from app.connectors.core.sync.sync_coordinator import Admission, SyncLease

        outcome = self.admission or Admission.GRANTED
        if outcome is not Admission.GRANTED:
            return outcome, None
        self.acquired.append(connector_id)
        return outcome, SyncLease(connector_id, "stub-token", 1)

    async def end(self, lease) -> bool:
        self.released.append(lease.connector_id)
        return True

    def running_count(self) -> int:
        return len(self.acquired) - len(self.released)


class _Installed:
    stub: StubCoordinator | None = None


@contextlib.contextmanager
def installed_stub() -> Iterator[StubCoordinator]:
    """Patch a fresh stub in as EventService's coordinator for one test."""
    _Installed.stub = StubCoordinator()
    try:
        with patch("app.connectors.services.event_service.get_coordinator", return_value=_Installed.stub):
            yield _Installed.stub
    finally:
        _Installed.stub = None


def current() -> StubCoordinator:
    """The stub the running test installed, for tests that do not take the fixture."""
    assert _Installed.stub is not None, "no coordinator stub is installed"
    return _Installed.stub


@contextlib.contextmanager
def current_coordinator() -> Iterator[StubCoordinator]:
    yield current()


@contextlib.contextmanager
def at_capacity() -> Iterator[StubCoordinator]:
    """Make the installed stub answer AT_CAPACITY."""
    from app.connectors.core.sync.sync_coordinator import Admission

    stub = current()
    stub.admission = Admission.AT_CAPACITY
    try:
        yield stub
    finally:
        stub.admission = None

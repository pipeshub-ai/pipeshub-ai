"""Fixtures for the Atlassian connector behaviour tests (fakes live in atlassian_behaviour_fakes)."""

import pytest
from atlassian_behaviour_fakes import (
    AtlassianApiStub,
    FakeCheckpointStore,
    FakeRecordsDb,
)


@pytest.fixture
def atlassian_api() -> AtlassianApiStub:
    return AtlassianApiStub()


@pytest.fixture
def records_db() -> FakeRecordsDb:
    return FakeRecordsDb()


@pytest.fixture
def checkpoints() -> FakeCheckpointStore:
    return FakeCheckpointStore()


@pytest.fixture(autouse=True)
def backoff_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Retry backoff is recorded instead of slept, so retries stay fast and assertable."""
    slept: list[float] = []

    async def _sleep(delay: float, *_: object, **__: object) -> None:
        slept.append(delay)

    monkeypatch.setattr("app.sources.client.http.http_retry.asyncio.sleep", _sleep)
    return slept

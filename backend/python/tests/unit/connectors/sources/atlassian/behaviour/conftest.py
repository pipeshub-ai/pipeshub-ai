"""Fixtures for the Atlassian connector behaviour tests (fakes live in atlassian_behaviour_fakes)."""

import asyncio

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

    class _BackoffAsyncio:
        # asyncio as the retrying modules see it, with only their sleep recorded.
        # Patching ``http_retry.asyncio.sleep`` replaced asyncio.sleep for the whole
        # process, so a task that sleeps between ticks spun without ever yielding.
        sleep = staticmethod(_sleep)

        def __getattr__(self, name: str) -> object:
            return getattr(asyncio, name)

    for module in (
        "app.sources.client.http.http_retry",
        "app.sources.client.http.http_resilient_transport",
        "app.connectors.sources.atlassian.jira_cloud.connector",
        "app.connectors.sources.atlassian.jira_data_center.connector",
    ):
        monkeypatch.setattr(f"{module}.asyncio", _BackoffAsyncio())
    return slept

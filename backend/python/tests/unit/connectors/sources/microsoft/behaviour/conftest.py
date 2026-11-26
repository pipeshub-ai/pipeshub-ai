"""Fixtures for the Microsoft 365 connector behaviour tests (fakes live in ms_graph_fakes)."""

import asyncio
import types

import aiohttp
import httpx
import pytest
import requests
from ms_graph_fakes import FakeCheckpointStore, FakeRecordsDb, MicrosoftCloudStub


@pytest.fixture
def cloud() -> MicrosoftCloudStub:
    return MicrosoftCloudStub()


@pytest.fixture
def db() -> FakeRecordsDb:
    return FakeRecordsDb()


@pytest.fixture
def checkpoints() -> FakeCheckpointStore:
    return FakeCheckpointStore()


@pytest.fixture(autouse=True)
def backoff_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Graph SDK retry delays are recorded instead of slept."""
    slept: list[float] = []

    async def _sleep(delay: float, *_: object, **__: object) -> None:
        slept.append(delay)

    # The retry handler's own `asyncio` only: patching `asyncio.sleep` itself also made the connector's resilience
    # gate (it waits a throttle's pause out on the loop clock) spin, as an instant sleep never moves that clock.
    retry_asyncio = types.SimpleNamespace(**{**vars(asyncio), "sleep": _sleep})
    monkeypatch.setattr("kiota_http.middleware.retry_handler.asyncio", retry_asyncio)
    # The gate's pause is the same wait the retry handler records; here the connector's other callers are none.
    monkeypatch.setattr("app.sources.client.resilience.ResiliencePolicy.pause", lambda *_, **__: None)
    return slept


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request that slipped past the stub fails the test instead of reaching Microsoft."""

    def _refuse(*args: object, **_: object) -> None:
        raise AssertionError(f"real network call attempted: {args[1:2]}")

    async def _refuse_async(*args: object, **kwargs: object) -> None:
        _refuse(*args, **kwargs)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _refuse)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", _refuse_async)

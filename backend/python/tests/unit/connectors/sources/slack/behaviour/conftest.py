"""Fixtures for the Slack connector behaviour tests (fakes live in slack_behaviour_fakes)."""

import asyncio
import socket

import httpx
import pytest
import slack_sdk.web.base_client
from slack_behaviour_fakes import FakeCheckpoints, FakeSlackStore, SlackWorkspace

_real_async_client = httpx.AsyncClient
_real_sleep = asyncio.sleep


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to reach a real host fails the test instead of leaving the machine."""
    real_connect = socket.socket.connect

    def _guarded(self: socket.socket, address: object) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError(f"test tried to reach the network: {address!r}")
        real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded)


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> SlackWorkspace:
    """The fake workspace, wired under the real ``slack_sdk`` client and the file host."""
    workspace = SlackWorkspace()
    monkeypatch.setattr(slack_sdk.web.base_client, "urlopen", workspace.urlopen)

    class _FileHostClient(_real_async_client):
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = httpx.MockTransport(workspace.file_host)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _FileHostClient)
    return workspace


@pytest.fixture
def store() -> FakeSlackStore:
    return FakeSlackStore()


@pytest.fixture
def checkpoints() -> FakeCheckpoints:
    return FakeCheckpoints()


@pytest.fixture(autouse=True)
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Rate-limit waits are recorded instead of slept, so they stay fast and assertable."""
    slept: list[float] = []

    async def _sleep(delay: float, *args: object, **kwargs: object) -> None:
        if delay <= 0:
            await _real_sleep(0)
            return
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return slept

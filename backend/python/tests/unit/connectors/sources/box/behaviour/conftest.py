"""Fixtures for the Box connector behaviour tests (fakes live in box_behaviour_fakes)."""

from types import SimpleNamespace

import pytest
import requests
from box_behaviour_fakes import FakeBoxApi, FakeBoxRecordsDb, FakeCheckpointStore
from box_sdk_gen.networking import box_network_client, network


@pytest.fixture
def box_api(monkeypatch: pytest.MonkeyPatch) -> FakeBoxApi:
    """Every Box SDK client built during the test talks to this fake instead of the network."""
    api = FakeBoxApi()
    session = requests.Session()
    session.mount("https://", api)
    session.mount("http://", api)
    monkeypatch.setattr(network, "BoxNetworkClient", lambda: box_network_client.BoxNetworkClient(requests_session=session))
    return api


@pytest.fixture(autouse=True)
def sdk_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The SDK's retry waits are recorded instead of slept, so retries stay fast and assertable."""
    slept: list[float] = []
    monkeypatch.setattr(box_network_client, "time", SimpleNamespace(sleep=slept.append))
    return slept


@pytest.fixture
def db() -> FakeBoxRecordsDb:
    return FakeBoxRecordsDb()


@pytest.fixture
def checkpoints() -> FakeCheckpointStore:
    return FakeCheckpointStore()

"""CH-16: the engine worker's broker retries failing creates, and fails over to ``sandboxBroker.fallbackEndpoint``."""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.sandbox import client as broker_client
from harness_core.sandbox.failover import FailoverSandboxBroker
from harness_core.sandbox.types import RuntimeMode


class _Remote:
    def __init__(self, url: str) -> None:
        self.url = url

    def modes(self) -> frozenset[RuntimeMode]:
        return frozenset({RuntimeMode.DOCKER_HARDENED})


class _Config:
    def __init__(self, endpoints: dict[str, Any]) -> None:
        self._endpoints = endpoints

    async def get_config(self, key: str, **_: object) -> object:
        return self._endpoints if key.endswith("endpoints") else None


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []

    async def open_remote(url: str, auth: object, **_: object) -> _Remote:
        del auth
        if "down" in url:
            raise OSError("connection refused")
        urls.append(url)
        return _Remote(url)

    monkeypatch.setattr(broker_client.RemoteSandboxBroker, "open", open_remote)
    return urls


def _providers(broker: object) -> list[str]:
    assert isinstance(broker, FailoverSandboxBroker)
    return [p.broker.url for p in broker._providers]  # type: ignore[attr-defined]


async def test_ch16_a_configured_fallback_broker_is_the_second_provider(opened: list[str]) -> None:
    config = _Config({"sandboxBroker": {"endpoint": "http://primary:8095", "fallbackEndpoint": "http://other:8095"}})
    broker = await broker_client.open_engine_broker(config)  # type: ignore[arg-type]
    assert _providers(broker) == ["http://primary:8095", "http://other:8095"]


async def test_ch16_without_a_fallback_the_one_broker_still_retries_its_creates(opened: list[str]) -> None:
    broker = await broker_client.open_engine_broker(_Config({"sandboxBroker": {"endpoint": "http://primary:8095"}}))  # type: ignore[arg-type]
    assert _providers(broker) == ["http://primary:8095"]


async def test_ch16_a_fallback_that_cannot_be_opened_leaves_the_primary_alone(opened: list[str]) -> None:
    config = _Config({"sandboxBroker": {"endpoint": "http://primary:8095", "fallbackEndpoint": "http://down:8095"}})
    broker = await broker_client.open_engine_broker(config)  # type: ignore[arg-type]
    assert _providers(broker) == ["http://primary:8095"]

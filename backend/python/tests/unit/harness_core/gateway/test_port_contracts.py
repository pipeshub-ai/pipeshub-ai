"""The gateway's in-memory adapters against their contract suites."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.gateway import InMemoryCitationSink, StaticGatewayKeySource
from harness_core.testing.contracts.gateway import (
    CitationSinkContract,
    GatewayKeySourceContract,
)

if TYPE_CHECKING:
    from harness_core.gateway.ports import CitationSink, PublishedKeys


class MemoryCitations:
    def __init__(self) -> None:
        self._sink = InMemoryCitationSink()

    @property
    def sink(self) -> CitationSink:
        return self._sink

    def label(self, url: str) -> str:
        return self._sink.ref_for(url)


class TestInMemoryCitationSink(CitationSinkContract):
    @pytest.fixture
    def citation_harness(self) -> MemoryCitations:
        return MemoryCitations()


class StaticKeys:
    def __init__(self) -> None:
        self.source = StaticGatewayKeySource()

    async def publish(self, keys: PublishedKeys) -> None:
        self.source.publish(keys)


class TestStaticGatewayKeySource(GatewayKeySourceContract):
    @pytest.fixture
    def key_harness(self) -> StaticKeys:
        return StaticKeys()

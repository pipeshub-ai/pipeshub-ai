"""CP-*, ES-*, TG-* (E0): the controller's ports on their in-memory adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.checkpoints import ObjectStoreEngineCheckpoints
from harness_core.engines.memory import InMemoryEngineEventSink
from harness_core.testing.contracts.engine_controller import (
    EngineCheckpointStoreContract,
    EngineEventSinkContract,
    ToolGatewayHostContract,
)
from harness_core.testing.engines.tool_host import InMemoryToolGatewayHost
from harness_core.testing.sandbox.egress import EgressRequest
from harness_core.workspace.memory import InMemoryBinaryObjectStore, InMemoryKeyProvider
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.engines.events import EngineEvent
    from harness_core.gateway.types import GatewayBinding
    from harness_core.interactions.ports import RunBinding

SCOPE = ObjectScope("org-1", "ws:session-1")


@dataclass
class _Plain:
    inner: InMemoryBinaryObjectStore = field(default_factory=InMemoryBinaryObjectStore)
    scope: ObjectScope = SCOPE

    @property
    def store(self) -> ObjectStoreEngineCheckpoints:
        return ObjectStoreEngineCheckpoints(self.inner)

    def tamper(self) -> None:
        self.inner.tamper()

    def lose(self) -> None:
        self.inner._objects.clear()  # pyright: ignore[reportPrivateUsage]

    async def shred(self) -> bool:
        return False


@dataclass
class _Sealed(_Plain):
    keys: InMemoryKeyProvider = field(default_factory=InMemoryKeyProvider)

    @property
    def store(self) -> ObjectStoreEngineCheckpoints:
        return ObjectStoreEngineCheckpoints(SealedObjectStore(self.inner, self.keys))

    async def shred(self) -> bool:
        assert self.scope.partition is not None
        await self.keys.destroy(self.scope.tenant_id, self.scope.partition)
        return True


class TestCheckpointsOnAPlainStore(EngineCheckpointStoreContract):
    @pytest.fixture
    def checkpoint_env(self) -> _Plain:
        return _Plain()


class TestCheckpointsSealedWithTheSessionKey(EngineCheckpointStoreContract):
    @pytest.fixture
    def checkpoint_env(self) -> _Sealed:
        return _Sealed()


@dataclass
class _Sink:
    sink: InMemoryEngineEventSink = field(default_factory=InMemoryEngineEventSink)

    async def recorded(self, run: RunBinding) -> Sequence[EngineEvent]:
        return [event for bound, event in self.sink.events if bound == run]

    async def saves(self, run: RunBinding) -> Sequence[int]:
        return [s.manifest.revision for s in self.sink.saves if s.run == run]


class TestInMemoryEngineEventSink(EngineEventSinkContract):
    @pytest.fixture
    def sink_env(self) -> _Sink:
        return _Sink()


@dataclass
class _Host:
    host: InMemoryToolGatewayHost = field(default_factory=InMemoryToolGatewayHost)

    async def call(
        self, upstream: str, binding: GatewayBinding, name: str, arguments: Mapping[str, JsonValue]
    ) -> str | None:
        request = EgressRequest(
            url=f"{upstream}/mcp",
            method="POST",
            headers={},
            body={"method": "tools/call", "params": {"name": name, "arguments": dict(arguments)}},
        )
        reply = await self.host.handle(request)
        assert isinstance(reply, dict)
        if reply.get("content") == "no such binding":
            return None
        return str(reply["content"])


class TestInMemoryToolGatewayHost(ToolGatewayHostContract):
    @pytest.fixture
    def host_env(self) -> _Host:
        return _Host()

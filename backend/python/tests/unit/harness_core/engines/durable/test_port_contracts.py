"""The durable engine kernel's ports against their contracts (C-2): ``SilentPresenter``, the test world's
``RecordingPresenter`` and ``MemoryEngineAssembler``."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from harness_core.engines.durable.ports import EngineAttempt, SilentPresenter
from harness_core.testing.contracts.engine_kernel import (
    EngineRunPresenterContract,
    EngineRuntimeAssemblerContract,
)
from harness_core.testing.engines.durable import (
    EngineWorld,
    MemoryEngineAssembler,
    RecordingPresenter,
)


class TestSilentPresenter(EngineRunPresenterContract):
    @pytest.fixture
    def presenter(self) -> SilentPresenter:
        return SilentPresenter()


class TestRecordingPresenter(EngineRunPresenterContract):
    @pytest.fixture
    def presenter(self) -> RecordingPresenter:
        return RecordingPresenter()


@dataclass
class _Env:
    world: EngineWorld = field(default_factory=EngineWorld)

    @property
    def assembler(self) -> MemoryEngineAssembler:
        return MemoryEngineAssembler(self.world)

    async def attempt(self) -> EngineAttempt:
        self.world.admit("run-contract", "say hi")
        return await self.world.engine_attempt("run-contract")


class TestMemoryEngineAssembler(EngineRuntimeAssemblerContract):
    @pytest.fixture
    def assembler_env(self) -> _Env:
        return _Env()

"""PipesHub's engine-run adapters under their ports' contracts.

QA19-8: ``TaskCardPublicationLog`` runs the ``PublicationLogContract``; each run's log is the one
``PipesHubEngineAssembler`` builds for an attempt (the attempt's fenced commit and run events, the production
presenter), over a journal that validates PipesHub's events as Node's catalog does. QA19-7: the assembler itself runs
the ``EngineRuntimeAssemblerContract``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.contracts.generated.pipeshub.event_catalog import (
    EVENT_MODELS,
    EVENT_WRITERS,
)
from app.harness.engines.durable import (
    DurableEngineServices,
    PipesHubEngineAssembler,
    TaskCardPublicationLog,
)
from app.harness.engines.presenter import PipesHubEngineRunPresenter
from harness_core.clock import SystemClock
from harness_core.contracts import dump_contract
from harness_core.engines.controller import EngineControllerConfig
from harness_core.engines.durable.kernel import EngineKernel
from harness_core.engines.registry import AllowlistEngineRegistry
from harness_core.ids import RandomIds
from harness_core.testing import ModelScript, Reply, Turn
from harness_core.testing.contracts.artifacts import PublicationLogContract
from harness_core.testing.contracts.engine_kernel import EngineRuntimeAssemblerContract
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.durable import (
    FAKE_VERSION,
    IMAGE,
    EngineWorld,
    engine_profile,
    engine_run_spec,
)
from harness_core.testing.engines.fake_engine import FakeEngineAdapter
from harness_core.testing.engines.tool_host import InMemoryToolGatewayHost
from harness_core.testing.sandbox.fake import FakeSandboxBroker
from harness_core.testing.scripted_model import ScriptedModel
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from tests.unit.harness.assembly import world
from tests.unit.harness.test_node_journal_contract import (
    NodeHarness,
    node_server,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.artifacts.types import (
        ArtifactCard,
        PublicationHistory,
        PublicationRecord,
        PublishIntent,
    )
    from harness_core.engines.durable.ports import EngineAttempt
    from harness_core.interactions.ports import RunBinding
    from tests.unit.harness.test_node_journal_contract import (
        _NodeServer,  # pyright: ignore[reportPrivateUsage]
    )


# The org the Node test server admits contract runs under (``NodeHarness``).
NODE_ORG = "org-contract"


@dataclass
class _PerAttempt:
    """A ``TaskCardPublicationLog`` per run, each bound to that run's claimed attempt."""

    world: EngineWorld = field(
        default_factory=lambda: EngineWorld(
            extension_writers=cast("Any", EVENT_WRITERS), extension_models=EVENT_MODELS
        )
    )
    logs: dict[str, TaskCardPublicationLog] = field(default_factory=dict[str, TaskCardPublicationLog])

    async def _for(self, binding: RunBinding) -> TaskCardPublicationLog:
        if binding.run_id not in self.logs:
            self.world.admit(binding.run_id, "contract", session=binding.scope)
            attempt = await self.world.engine_attempt(binding.run_id)
            self.logs[binding.run_id] = TaskCardPublicationLog(attempt, PipesHubEngineRunPresenter())
        return self.logs[binding.run_id]

    async def intent(self, binding: RunBinding, intent: PublishIntent) -> None:
        await (await self._for(binding)).intent(binding, intent)

    async def published(self, binding: RunBinding, record: PublicationRecord) -> None:
        await (await self._for(binding)).published(binding, record)

    async def presented(self, binding: RunBinding, card: ArtifactCard) -> None:
        await (await self._for(binding)).presented(binding, card)

    async def history(self, binding: RunBinding) -> PublicationHistory:
        return await (await self._for(binding)).history(binding)


class TestTaskCardPublicationLog(PublicationLogContract):
    @pytest.fixture
    def publication_log(self) -> _PerAttempt:
        return _PerAttempt()


class _NodeAssemblerEnv:
    """QA19-7 (ENG-G2, TGW-G5): ``PipesHubEngineAssembler`` as a worker runs it, over a run claimed from a live Node
    (its attempt route, content and memory routes); the services behind it are the assembly world's fakes."""

    def __init__(self, server: _NodeServer) -> None:
        self._node = NodeHarness(server)
        self._scope = uuid.uuid4().hex[:8]
        engines = DurableEngineServices(
            broker=FakeSandboxBroker(clock=SystemClock(), ids=RandomIds()),
            tool_host=InMemoryToolGatewayHost(),
            engines=AllowlistEngineRegistry([FakeEngineAdapter()], [FAKE_VERSION]),
            config=EngineControllerConfig(images={FAKE_VERSION: IMAGE}),
            objects=InMemoryBinaryObjectStore(),
            route_for=self._node.node.attempt_route,
            clock=SystemClock(),
            placeholder_key=b"k" * 32,
        )
        self.assembler = PipesHubEngineAssembler(
            world.Services(org=NODE_ORG), engines, model_factory=world.model_factory(ScriptedModelPort(ScriptedModel(ModelScript(id="assembler", turns=[Turn(reply=Reply(text="ok"))]))))
        )

    async def attempt(self) -> EngineAttempt:
        run_id = f"run-{self._scope}"
        executor = engine_run_spec().executor
        assert executor is not None
        spec = world.pinned_spec(tool_ids=[f"jira@{world.JIRA}/search_issues"], executor=dump_contract(executor))
        self._node.admit(run_id, session_id=f"session-{run_id}", input_text="go", spec=spec)
        claim = await self._node.journal.claim(run_id, worker_id="worker-assembler")
        assert claim is not None
        kernel = EngineKernel(journal=self._node.node, assembler=self.assembler, clock=SystemClock())
        return kernel.attempt_of(claim, engine_profile())


@pytest.fixture
def _deployment() -> Iterator[None]:
    with world.deployment():
        yield


class TestPipesHubEngineAssembler(EngineRuntimeAssemblerContract):
    @pytest.fixture
    def assembler_env(self, node_server: _NodeServer, _deployment: None) -> _NodeAssemblerEnv:
        return _NodeAssemblerEnv(node_server)


__all__ = ["node_server"]

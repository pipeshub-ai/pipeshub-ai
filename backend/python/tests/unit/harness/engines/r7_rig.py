"""recs2 item 3 (R7 end to end): a deep-mode root run on the real PipesHub assembler hands a coding subtask to an
engine child through ``code_task``, and gets the result back.

One journal (the PipesHub in-memory journal with the org's engine authority and the session's workspace store, as
Node holds both), one content route for every run, one ``RunDriver`` with both kernels: the loop for the parent
(``PipesHubRuntimeAssembler`` in deep mode, the orchestrator played by a scripted model) and the engine kernel for
the child. ``engine`` picks the child's engine: ``EngineWorld``'s scripted fake engine, or (``RealChild``) the real
opencode binary in the Docker broker's sandbox behind the real egress proxy and tool gateway (E0's real lane).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.harness.adapters.content_store import PipesHubContentStore
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import RunDriver
from harness_core.engines.durable.kernel import ENGINE
from harness_core.testing import ModelScript, Reply, ScriptedModel, ToolCallSpec, Turn
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.durable import EngineWorld, MemoryEngineAssembler
from harness_core.workspace.forks import StoreChildWorkspaces
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from harness_core.engines.durable.ports import AssembledEngineRun, EngineAttempt
    from harness_core.journal.types import Claim, JournalEvent

PARENT = "run-deep"


def engine_authority(kind: str, route: str = "route-azure-gpt") -> dict[str, Any]:
    """The org's engine profile for code_task children, as Node resolves it (the lane's pinned release)."""
    from tests.unit.harness.engines.real_engine_world import real_profile

    return real_profile(kind, route)


def deep_spec(profile: dict[str, Any]) -> RunSpec:
    spec = w.pinned_spec(tool_ids=[], sandbox="agent-code")
    raw = spec.model_dump(by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["codeTask"] = {"profile": profile}
    raw["extensions"]["x.pipeshub"]["mounts"] = ["/workspace"]
    raw["budgets"]["maxChildren"] = 2
    return validate_contract(RunSpec, raw)


def orchestrator(goal: str, answer: str) -> ScriptedModelPort:
    """The deep orchestrator: hands ``goal`` to a coding engine, then answers with what came back."""
    calls = [ToolCallSpec(name="code_task", args={"goal": goal, "task_id": "report"})]
    return ScriptedModelPort(
        ScriptedModel(ModelScript(id="deep", turns=[Turn(reply=Reply(tool_calls=calls)), Turn(reply=Reply(text=answer))]))
    )


@dataclass
class _DeepClaims:
    """Node's claim for the parent: the run snapshot of a deep-mode chat, on the journal's content route."""

    inner: PipesHubRuntimeAssembler

    async def build(self, attempt: Claim) -> Any:  # noqa: ANN401
        node = w.claim(attempt.spec, query=attempt.input_text, run_id=attempt.run_id, chat_mode="deep")
        values = {f.name: getattr(attempt, f.name) for f in dataclasses.fields(attempt)}
        return await self.inner.build(type(node)(**values, run=node.run))


@dataclass
class R7Rig:
    world: EngineWorld
    port: ScriptedModelPort
    engine_assembler: Any = None
    broker: Any = None
    driver: RunDriver = field(init=False)

    def __post_init__(self) -> None:
        from app.harness.contracts.generated.pipeshub.event_catalog import (
            EVENT_MODELS,
            EVENT_WRITERS,
        )
        from harness_core.testing.engines.durable import PROGRESS

        world = self.world
        world.journal = w.PipesHubInMemoryJournal(
            clock=world.clock, lease_ms=world.lease_ms, workspaces=world.store,
            engine_authority=world.engine_authority,
            extension_writers={**EVENT_WRITERS, PROGRESS: frozenset({"worker"})},
            extension_models=EVENT_MODELS,
        )
        assembler = self.engine_assembler or SharedContentAssembler(world)
        kernel = world.kernel(assembler=assembler, **({"broker": self.broker} if self.broker is not None else {}))
        parent = PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(self.port))
        self.driver = RunDriver(
            journal=world.journal,
            assembler=_DeepClaims(parent),
            clock=world.clock,
            children=world.journal,
            child_workspaces=StoreChildWorkspaces(lambda _token: (world.store, world.writers)),
            interactions=world.wakeup,
            kernels={ENGINE: kernel},
        )

    def admit(self, text: str) -> None:
        self.world.journal.admit(
            PARENT, org_id=w.ORG, session_id="ses-1", spec=deep_spec(self.world.engine_authority), input_text=text,
        )

    async def attempt(self, run_id: str) -> Any:  # noqa: ANN401
        claim = await self.world.journal.claim(run_id, worker_id="w-r7")
        assert claim is not None, f"{run_id} is not claimable"
        return await self.driver.run_claimed(claim, claim_sent_at=self.world.clock.suspend_aware())

    def events(self, stream: str = "ses-1") -> list[JournalEvent]:
        return list(self.world.journal.events(stream))

    def child(self) -> str:
        (child,) = self.world.journal.children_of(PARENT)
        return child

    async def result_of(self, event: JournalEvent) -> Any:  # noqa: ANN401
        ref = event.data.get("resultRef")
        content = PipesHubContentStore(self.world.journal.content)
        from harness_core.contracts.generated.common import ContentRef

        return json.loads(await content.get(ContentRef.model_validate(ref)))


class SharedContentAssembler(MemoryEngineAssembler):
    """``EngineWorld``'s engine assembler, writing to the content route every run of the journal shares."""

    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        built = await super().build(attempt)
        return dataclasses.replace(built, content=PipesHubContentStore(self.world.journal.content))


__all__ = ["PARENT", "R7Rig", "SharedContentAssembler", "deep_spec", "engine_authority", "orchestrator"]

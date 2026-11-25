"""DEEPROUTE's rig: a recorded run's shape on the real harness (a quick agent chat with an engine authority pinned, the
real ``PipesHubRuntimeAssembler``, ``RunDriver`` and durable kernel over the in-memory journal, the code_task child on
the engine kernel), the parent's model that fetches one more page per turn, and the deployment it runs in.

``tests/unit/harness/engines/test_deeproute_replay.py`` drives it; the ENGBUDGET and SPENDCONT replays and RUN01's research
lane build on it.
"""

from __future__ import annotations

import dataclasses
import json
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.adapters.content_store import PipesHubContentStore
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import RunDriver
from harness_core.engines.durable.kernel import ENGINE
from harness_core.engines.profiles import profile_json
from harness_core.kernel.core.messages import AssistantMessage, TextPart, ToolCall
from harness_core.kernel.core.responses import StopReason, TokenUsage
from harness_core.kernel.durable.harness_channel import HARNESS_FRAME
from harness_core.kernel.durable.ports import ModelFailed, SampledTurn
from harness_core.testing.engines.durable import (
    EngineWorld,
    MemoryEngineAssembler,
    engine_profile,
)
from harness_core.workspace.forks import StoreChildWorkspaces
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from harness_core.engines.durable.ports import AssembledEngineRun, EngineAttempt
    from harness_core.journal.types import Claim, JournalEvent
    from harness_core.kernel.durable.ports import DeltaKind, ModelCall

PARENT = "run-deeproute"
JIRA_SEARCH = f"jira@{w.JIRA}/search_issues"
CODE_TASK_ID = "delegate@default/code_task"
# The recorded request, as the router reads it (and a narrower one the router keeps in the loop).
RECORDED_REQUEST = (
    "many open pull requests are pending on the pipeshub-ai oss repo, which need fixing and give immediate value. "
    "find them and tell me which to merge first"
)
NARROW = "check the open pull requests on the pipeshub-ai repo for me"
# The reply's budget (the release default ``maxTokens``) and the recorded run as query.log has it: fetches per model
# turn and each request's input tokens; past turn 7 the requests stay at its last size. Each fetch also spends on the
# page summariser's model (1.14M tokens charged, 272k of them the loop's own requests, 21 fetches: ~41k a fetch).
BUDGET = 1_000_000
RECORDED_TURNS = [(1, 8_860), (3, 9_607), (1, 11_254), (3, 12_456), (6, 31_913), (5, 77_469), (6, 121_172)]
LAST_REQUEST = 136_638
FETCH_SPEND = 41_000
ANSWER = "Merge PR 41 first: it fixes the crash on startup [PR 41](https://github.com/pipeshub-ai/pipeshub-ai/pull/41)."


@dataclass
class RecordedModel:
    """The recorded run's parent model, as a durable model port, under the reply's budget.

    Fetches one more page a turn (the request grows by a page each turn, as the recorded run's did), hands off when told
    to, and presents the engine's answer. ``obeys=False`` plays a model that ignores the hand-off instruction once."""

    obeys: bool = True
    spent: int = 0
    turns: int = 0
    charged: int = 0
    calls: list[ModelCall] = field(default_factory=list["ModelCall"])

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        self.calls.append(call)
        results = [m for m in call.messages if getattr(m, "role", None) == "tool"]
        fetched = sum(1 for m in results if "PR-page" in str(getattr(m, "content", "")))
        self.spent += FETCH_SPEND * (fetched - self.charged)
        self.charged = fetched
        _, size = RECORDED_TURNS[self.turns] if self.turns < len(RECORDED_TURNS) else (6, LAST_REQUEST)
        usage = TokenUsage(input_tokens=size, output_tokens=400)
        if self.spent + usage.input_tokens + usage.output_tokens > BUDGET:
            raise ModelFailed("budget_exhausted", "the reply's budget refused the call")
        self.spent += usage.input_tokens + usage.output_tokens
        self.turns += 1
        code_task = _code_task_result(call)
        if code_task is not None:
            output = str(code_task.get("output") or "")
            await on_delta("text_delta", output)
            return SampledTurn(message=AssistantMessage(content=[TextPart(text=output)]), usage=usage)
        asked = _asked_to_hand_off(call)
        if asked and self.obeys:
            brief = (
                "Triage the open pull requests of pipeshub-ai and say which to merge first.\n"
                "Found so far: PR 41 fixes a startup crash (https://github.com/pipeshub-ai/pipeshub-ai/pull/41).\n"
                "write notes/plan.md 1. list the open PRs 2. rank by value\n"
                f"usage 30000\nsay {ANSWER}"
            )
            return _calls(usage, [ToolCall(id=f"ct{self.turns}", name="code_task", arguments={"goal": brief})])
        self.obeys = self.obeys or asked
        count, _ = RECORDED_TURNS[self.turns - 1] if self.turns <= len(RECORDED_TURNS) else (6, LAST_REQUEST)
        return _calls(
            usage,
            [
                ToolCall(id=f"p{self.turns}.{i}", name="jira__search_issues", arguments={"jql": f"page {self.turns}.{i}"})
                for i in range(count)
            ],
        )

    def restore(self, raw: bytes, *, provider: str, transport: str) -> AssistantMessage | None:
        return None


def _calls(usage: TokenUsage, calls: list[ToolCall]) -> SampledTurn:
    return SampledTurn(
        message=AssistantMessage(content=[], tool_calls=calls), usage=usage, stop_reason=StopReason.TOOL_USE
    )


def _asked_to_hand_off(call: ModelCall) -> bool:
    """The turn's harness instructions ride its system channel (ADR-RUN-01 D5), never a message."""
    return HARNESS_FRAME in (call.system or "") and "call code_task once" in (call.system or "")


def _code_task_result(call: ModelCall) -> dict[str, Any] | None:
    ids = {
        tc.id
        for m in call.messages
        for tc in (getattr(m, "tool_calls", None) or [])
        if tc.name == "code_task"
    }
    for message in call.messages:
        if getattr(message, "role", None) == "tool" and getattr(message, "tool_call_id", None) in ids:
            content = message.content if isinstance(message.content, str) else ""
            try:
                parsed = json.loads(content[: content.rfind("}") + 1])
            except ValueError:
                return {"output": content}
            return parsed if isinstance(parsed, dict) else {"output": content}
    return None


def _spec(profile: Any, *, auto_route: bool = True) -> RunSpec:  # noqa: ANN401
    spec = w.pinned_spec(tool_ids=[JIRA_SEARCH], sandbox="agent-code")
    raw = spec.model_dump(by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["codeTask"] = {"profile": profile, **({} if auto_route else {"autoRoute": False})}
    raw["extensions"]["x.pipeshub"]["mounts"] = ["/workspace"]
    raw["budgets"]["maxChildren"] = 1
    raw["budgets"]["maxTokens"] = BUDGET
    return validate_contract(RunSpec, raw)


@dataclass
class _QuickClaims:
    inner: PipesHubRuntimeAssembler

    async def build(self, attempt: Claim) -> Any:  # noqa: ANN401
        node = w.claim(attempt.spec, query=attempt.input_text, run_id=attempt.run_id, chat_mode="quick")
        values = {f.name: getattr(attempt, f.name) for f in dataclasses.fields(attempt)}
        return await self.inner.build(type(node)(**values, run=node.run))


class _SharedContent(MemoryEngineAssembler):
    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        built = await super().build(attempt)
        return dataclasses.replace(built, content=PipesHubContentStore(self.world.journal.content))


@dataclass
class Rig:
    model: RecordedModel
    world: EngineWorld = field(
        default_factory=lambda: EngineWorld(engine_authority=profile_json(engine_profile(max_steps=40)))
    )
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
        parent = PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(self.model))
        self.driver = RunDriver(
            journal=world.journal,
            assembler=_QuickClaims(parent),
            clock=world.clock,
            children=world.journal,
            child_workspaces=StoreChildWorkspaces(lambda _token: (world.store, world.writers)),
            interactions=world.wakeup,
            kernels={ENGINE: world.kernel(assembler=_SharedContent(world))},
        )

    def admit(self, text: str, *, auto_route: bool = True) -> None:
        self.world.journal.admit(
            PARENT, org_id=w.ORG, session_id="ses-1", spec=_spec(self.world.engine_authority, auto_route=auto_route),
            input_text=text,
        )

    async def attempt(self, run_id: str) -> Any:  # noqa: ANN401
        claim = await self.world.journal.claim(run_id, worker_id="w-deeproute")
        assert claim is not None, f"{run_id} is not claimable"
        return await self.driver.run_claimed(claim, claim_sent_at=self.world.clock.suspend_aware())

    async def drive(self, text: str, *, auto_route: bool = True) -> Any:  # noqa: ANN401
        """The parent to its end, running each code_task child it joins on."""
        self.admit(text, auto_route=auto_route)
        done = await self.attempt(PARENT)
        while (done.status, done.reason) == ("suspended", "join"):
            for child in self.world.journal.children_of(PARENT):
                if not any(e.type == "run.completed" for e in self.events(child)):
                    finished = await self.attempt(child)
                    assert finished.status == "completed", finished
            done = await self.attempt(PARENT)
        return done

    def events(self, stream: str = "ses-1") -> list[JournalEvent]:
        return list(self.world.journal.events(stream))

    def parent(self, kind: str) -> list[JournalEvent]:
        return [e for e in self.events() if e.type == kind and e.run_id == PARENT]


@contextmanager
def deployment() -> Iterator[None]:
    """The deployment the rig's parent runs in (shared with RUN01's research lane, which needs it module-wide)."""
    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(
            patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, "ses-1"))
        )
        stack.enter_context(patch.dict("os.environ", {"PIPESHUB_ENABLE_LAZY_TOOLS": "false"}))

        async def page(self: object, jql: str) -> str:
            return json.dumps({"issues": [{"key": f"PR-{jql.replace(' ', '')}-{i}", "body": "x" * 400} for i in range(40)]})

        w.replace_tool(stack.enter_context(pytest.MonkeyPatch.context()), w.FakeJira, "search_issues", page)
        yield


__all__ = ["ANSWER", "BUDGET", "CODE_TASK_ID", "JIRA_SEARCH", "NARROW", "PARENT", "RECORDED_REQUEST", "RecordedModel", "Rig", "deployment"]

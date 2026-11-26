"""WP-62 (WP62-REPLAY-1, WP62-JRNL-1, WP62-PART-1): a recorded deep-research hand-off, replayed with a scripted engine.

In a manual test, the chat loop handed a multi-source research request to Codex through
``code_task``. Codex fetched pages in parallel batches from a script, got ``budget_exhausted`` refusals once 20 page
reads were spent, and had fetched two GitHub tree listings of 4.2 MB each. The stall detector stopped it; the cut-short
path then put a child result carrying every web block the run had read (8.7 MB) and Node refused it with 413
``CONTENT_TOO_LARGE``. The child failed, its fork (25 revisions, 83 files) was never merged, and the parent was told
the engine "returned no answer".

Here the parent is the real chat-loop assembler (its model scripted) and the child runs on the engine kernel with
``EngineWorld``'s scripted engine. The engine makes 40 calls in four parallel batches; the last 20 are refused as
spent allowance, one returns 4.2 MB, and the child's citation state holds the two uncut tree listings it had. The
content store refuses anything over 8 MiB, as Node does. The run must complete with the report, or, cut short by a
limit, complete flagged partial with its notes merged and its partial answer handed to the parent.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.web_tool_adapter import WebToolAdapter
from app.harness.adapters.content_store import PipesHubContentStore
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.engines.child_result import EngineChildResult
from app.harness.engines.durable import present_for_engines
from app.utils.chat_helpers import CitationRefMapper
from harness_core.contracts.generated.common import ContentRef
from harness_core.driver import RunDriver
from harness_core.effects.types import ToolExecution
from harness_core.engines.durable.kernel import ENGINE
from harness_core.engines.profiles import profile_json
from harness_core.journal.bounds import CONTENT_MAX_BYTES
from harness_core.journal.types import EventRejected
from harness_core.testing import ModelScript, Reply, ScriptedModel, ToolCallSpec, Turn
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.durable import EngineWorld, engine_profile
from harness_core.workspace.forks import StoreChildWorkspaces, session_scope
from tests.unit.harness.assembly import world as w
from tests.unit.harness.engines.r7_rig import PARENT, SharedContentAssembler, deep_spec

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.effects.types import ToolContext
    from harness_core.engines.durable.ports import AssembledEngineRun, EngineAttempt
    from harness_core.journal.types import Claim, JournalEvent
    from harness_core.kernel.core.messages import ToolCall

SESSION = "ses-1"
TREE = "https://api.github.com/repos/onyx-dot-app/onyx/git/trees/main?recursive=1"
OTHER_TREE = "https://api.github.com/repos/pipeshub-ai/pipeshub-ai/git/trees/main?recursive=1"
# A tree listing is JSON: escaped again inside a journal document, it grows.
TREE_TEXT = json.dumps({"tree": [{"path": f"backend/module_{i}/file_{i}.py", "type": "blob"} for i in range(60_000)]})
REFUSAL = json.dumps(
    {"ok": False, "error_type": "budget_exhausted", "error": "This task's page reads are used up", "retryable": False}
)
PAGES = 40
BATCH = 10
REPORT = "# Onyx vs PipesHub\\n\\nOnyx ships more connectors; PipesHub indexes permissions [source](ref1)."


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


class _Args(BaseModel):
    url: str


async def _researched_child_context() -> AgentContext:
    """The child's citation state as the recorded run had it: 20 pages read, two of them uncut 4.2 MB tree listings."""
    context = AgentContext(
        org_id=w.ORG, user_id="user-1", user_email="user@example.com",
        user_info={"userId": "user-1", "orgId": w.ORG}, org_info={"name": "TestOrg"},
        logger=MagicMock(), llm=MagicMock(), retrieval_service=MagicMock(), graph_provider=MagicMock(),
        config_service=MagicMock(), blob_store=MagicMock(),
    )
    context.tool_state["citation_ref_mapper"] = CitationRefMapper()
    present_for_engines(context)
    read: list[str] = []
    for n in range(PAGES // 2):
        url = TREE if n == 3 else OTHER_TREE if n == 7 else f"https://docs.example.com/page-{n}"
        text = TREE_TEXT if url in (TREE, OTHER_TREE) else f"Page {n}: PipesHub indexes permissions from each source."
        blocks = [{"type": "text", "content": text[i : i + 2000]} for i in range(0, len(text), 2000)]
        payload = {"ok": True, "result_type": "url_content", "url": url, "title": f"Page {n}", "blocks": blocks}

        async def _run(_payload: dict[str, Any] = payload, **_kwargs: Any) -> Any:  # noqa: ANN401
            return _payload

        tool = StructuredTool.from_function(name="fetch_url", description="fetch", args_schema=_Args, coroutine=_run)
        output = await WebToolAdapter(tool, "web", "fetch_url", context).execute(url=url)
        read.append(str(output.data))
    context.tool_state["wp62_read"] = read
    return context


class _ResearchingChild(SharedContentAssembler):
    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        built = await super().build(attempt)
        context = await _researched_child_context()
        return dataclasses.replace(built, child_result=EngineChildResult(context, lambda: context.tool_state["wp62_read"]))


@dataclasses.dataclass
class _ChatClaims:
    inner: PipesHubRuntimeAssembler

    async def build(self, attempt: Claim) -> Any:  # noqa: ANN401
        node = w.claim(attempt.spec, query=attempt.input_text, run_id=attempt.run_id, chat_mode="agent")
        values = {f.name: getattr(attempt, f.name) for f in dataclasses.fields(attempt)}
        return await self.inner.build(type(node)(**values, run=node.run))


class _NodeSizedContent:
    """The journal's content route with Node's limit: a put over 8 MiB is refused as Node's 413 is."""

    def __init__(self, inner: Any) -> None:  # noqa: ANN401
        self._inner = inner
        self.largest = 0

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)

    async def put(self, data: bytes) -> Any:  # noqa: ANN401
        self.largest = max(self.largest, len(data))
        if len(data) > CONTENT_MAX_BYTES:
            raise EventRejected("CONTENT_TOO_LARGE")
        return await self._inner.put(data)

    async def put_sealed(self, data: bytes) -> Any:  # noqa: ANN401
        return await self.put(data)


async def _fetch(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    index = int(str(call.arguments.get("url", "")).rsplit("/", 1)[-1])
    if index >= PAGES // 2:
        return ToolExecution(status="error", content=REFUSAL)
    if index == 3:
        return ToolExecution(status="ok", content=TREE_TEXT)
    return ToolExecution(status="ok", content=f"page {index}: PipesHub indexes permissions")


def _batches() -> list[str]:
    return [
        "tools lookup " + json.dumps([{"url": f"https://docs.example.com/{i}"} for i in range(start, start + BATCH)])
        for start in range(0, PAGES, BATCH)
    ]


def _goal(*, finish: bool) -> str:
    lines = [_batches()[0], "write notes/batch-1.md first ten pages read", *_batches()[1:]]
    if finish:
        lines += [f"write out/report.md {REPORT}", "say Report saved as out/report.md [source](ref1)"]
    return "\n".join(lines)


@dataclasses.dataclass
class _Ran:
    events: list[JournalEvent]
    child_events: list[JournalEvent]
    outcome: dict[str, Any]
    child_status: str
    content: _NodeSizedContent
    world: EngineWorld


async def _run(goal: str, *, max_tokens: int | None = None) -> _Ran:
    profile = engine_profile(max_steps=40, max_tokens=max_tokens)
    world = EngineWorld(engine_authority=profile_json(profile), handlers={"lookup": _fetch})
    world.journal = w.PipesHubInMemoryJournal(
        clock=world.clock, lease_ms=world.lease_ms, workspaces=world.store,
        engine_authority=world.engine_authority, extension_writers=_writers(), extension_models=_models(),
    )
    content = _NodeSizedContent(world.journal.content)
    world.journal.content = content
    parent_model = ScriptedModelPort(ScriptedModel(ModelScript(
        id="wp62",
        turns=[
            Turn(reply=Reply(tool_calls=[ToolCallSpec(name="code_task", args={"goal": goal, "task_id": "onyx"})])),
            Turn(reply=Reply(text="Here is the research (partial where marked).")),
        ],
    )))
    driver = RunDriver(
        journal=world.journal,
        assembler=_ChatClaims(PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(parent_model))),
        clock=world.clock,
        children=world.journal,
        child_workspaces=StoreChildWorkspaces(lambda _token: (world.store, world.writers)),
        interactions=world.wakeup,
        kernels={ENGINE: world.kernel(assembler=_ResearchingChild(world))},
    )
    world.journal.admit(PARENT, org_id=w.ORG, session_id=SESSION, spec=deep_spec(world.engine_authority), input_text="research")

    async def attempt(run_id: str) -> Any:  # noqa: ANN401
        claim = await world.journal.claim(run_id, worker_id="w-wp62")
        assert claim is not None, run_id
        return await driver.run_claimed(claim, claim_sent_at=world.clock.suspend_aware())

    assert (await attempt(PARENT)).status == "suspended"
    (child,) = world.journal.children_of(PARENT)
    child_done = await attempt(child)
    assert child_done.status == "completed", child_done
    done = await attempt(PARENT)
    assert done.status == "completed", done
    events = list(world.journal.events(SESSION))
    (outcome,) = [e for e in events if e.type == "tool.outcome" and e.run_id == PARENT]
    read = await PipesHubContentStore(content).get(ContentRef.model_validate(outcome.data["resultRef"]))
    return _Ran(events, list(world.journal.events(child)), json.loads(read.decode("utf-8"))["content"], child_done.status, content, world)


def _writers() -> dict[str, frozenset[str]]:
    from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_WRITERS
    from harness_core.testing.engines.durable import PROGRESS

    return {**EVENT_WRITERS, PROGRESS: frozenset({"worker"})}


def _models() -> dict[str, Any]:
    from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS

    return dict(EVENT_MODELS)


async def _session_files(world: EngineWorld) -> set[str]:
    from harness_core.workspace.forks import session_workspace_id

    scope = session_scope(w.ORG, SESSION)
    head = await world.store.head(scope, session_workspace_id(SESSION))
    revision = await world.store.revision(scope, head.workspace_id, head.revision)
    return set(revision.files)


async def test_wp62_replay_1_forty_fetches_in_batches_with_refusals_and_a_huge_page_complete_with_the_report() -> None:
    ran = await _run(_goal(finish=True))
    assert ran.child_status == "completed"
    assert ran.content.largest <= CONTENT_MAX_BYTES
    assert sum(ran.world.effects.values()) == PAGES
    completed = [e.data for e in ran.child_events if e.type == "run.completed"]
    assert completed == [{"outcome": "answered"}]
    assert ran.outcome.get("success") is True, ran.outcome
    assert "report.md" in str(ran.outcome.get("output"))
    assert {"out/report.md", "notes/batch-1.md"} <= await _session_files(ran.world)


async def test_wp62_part_1_a_child_a_limit_stops_completes_partial_and_its_notes_reach_the_parent() -> None:
    """SPENDCONT's budget cut, on that shape: the child completes flagged, its fork is merged, its answer is partial."""
    # The 4.2 MB page read in the first batch takes the next model call past the run's token limit: the controller
    # stops the engine as a spent budget stops it, after the notes of the first batch were written.
    ran = await _run(_goal(finish=True), max_tokens=500_000)
    assert ran.child_status == "completed"
    assert ran.content.largest <= CONTENT_MAX_BYTES
    completed = [e.data for e in ran.child_events if e.type == "run.completed"]
    assert completed == [{"outcome": "answered", "truncatedBy": "budget"}]
    assert ran.outcome.get("success") is True, ran.outcome
    assert ran.outcome.get("truncatedBy") == "budget"
    assert "partial" in str(ran.outcome.get("stopped_early"))
    workspace = ran.outcome.get("workspace") or {}
    assert workspace.get("status") == "merged", workspace
    assert "notes/batch-1.md" in await _session_files(ran.world)

"""WP70-E2E: a deep-research run replayed: research cut at the step allowance still ends with a cited report.md.

In a manual test, the chat loop handed the Onyx-vs-PipesHub request to Codex. Codex wrote
a 178-byte "Research in progress" report.md, read 166 pages and was cut by the 50-step cap with a progress remark as
its last message. The wrap-up never ran, the placeholder was finalized, and the parent relayed four sentences.

Here the parent is the real chat-loop assembler (its model scripted, as WP62-REPLAY's) and the child runs on the engine
kernel with ``EngineWorld``'s scripted engine on a 16-step allowance: it writes the placeholder, reads pages (each a
``fetch_url`` result with its URL, recorded in its citation state as the web adapter records them), keeps a note, and
is still reading when the allowance ends. Its wrap-up is a scripted model over the real prompt. The run must deliver
report.md, cited and final, and a parent answer that names it and cites its sources.
"""

from __future__ import annotations

import dataclasses
import json
import re
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
from harness_core.interactions.ports import RunBinding
from harness_core.testing import ModelScript, Reply, ScriptedModel, ToolCallSpec, Turn
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.durable import EngineWorld, engine_profile
from harness_core.testing.engines.scripted_wrap_up import ScriptedWrapUp
from harness_core.workspace.forks import (
    StoreChildWorkspaces,
    session_scope,
    session_workspace_id,
)
from tests.unit.harness.assembly import world as w
from tests.unit.harness.engines.r7_rig import PARENT, SharedContentAssembler, deep_spec
from tests.unit.harness.engines.test_wp62_deep_research_replay import (
    _ChatClaims,
    _models,
    _writers,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.effects.types import ToolContext
    from harness_core.engines.durable.ports import AssembledEngineRun, EngineAttempt
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.core.messages import ToolCall

SESSION = "ses-1"
PAGES = 28
STEPS = 16
REQUEST = (
    "Do a deep research on how Onyx (onyx.app) compares with PipesHub. Cite every finding with its source URL. "
    "Write the final report as one Markdown file named report.md in your output folder."
)
PLACEHOLDER = "# Onyx vs PipesHub\\n\\nResearch in progress. Final report will follow."


def _url(n: int) -> str:
    return f"https://docs.onyx.app/page-{n}"


def _page(n: int) -> dict[str, Any]:
    return {
        "ok": True, "result_type": "url_content", "url": _url(n), "title": f"Onyx docs {n}",
        "blocks": [{"type": "text", "content": f"Onyx feature {n}: connector number {n} with permission sync."}],
    }


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


class _Args(BaseModel):
    url: str


async def _research_context() -> AgentContext:
    """The child's citation state: every page it reads, recorded as the web tools' adapter records a read."""
    context = AgentContext(
        org_id=w.ORG, user_id="user-1", user_email="user@example.com",
        user_info={"userId": "user-1", "orgId": w.ORG}, org_info={"name": "TestOrg"},
        logger=MagicMock(), llm=MagicMock(), retrieval_service=MagicMock(), graph_provider=MagicMock(),
        config_service=MagicMock(), blob_store=MagicMock(),
    )
    context.tool_state["citation_ref_mapper"] = CitationRefMapper()
    present_for_engines(context)
    read: list[str] = []
    for n in range(PAGES):

        async def _run(_payload: dict[str, Any] = _page(n), **_kwargs: Any) -> Any:  # noqa: ANN401
            return _payload

        tool = StructuredTool.from_function(name="fetch_url", description="fetch", args_schema=_Args, coroutine=_run)
        read.append(str((await WebToolAdapter(tool, "web", "fetch_url", context).execute(url=_url(n))).data))
    context.tool_state["wp70_read"] = read
    return context


class _ResearchingChild(SharedContentAssembler):
    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        built = await super().build(attempt)
        context = await _research_context()
        return dataclasses.replace(built, child_result=EngineChildResult(context, lambda: context.tool_state["wp70_read"]))


async def _fetch(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    del ctx
    n = int(str(call.arguments.get("url")).rsplit("-", 1)[-1])
    return ToolExecution(status="ok", content=json.dumps(_page(n)))


def _goal() -> str:
    reads = [f'tool lookup {json.dumps({"url": _url(n)})}' for n in range(PAGES)]
    return "\n".join([
        REQUEST,
        f"write out/report.md {PLACEHOLDER}",
        *reads[:4],
        "write notes/progress.md Onyx lists its connectors; PipesHub documents permission-aware indexing.",
        *reads[4:],
        "say I'm focusing on demonstrated differences rather than marketing claims.",
    ])


async def _run() -> tuple[EngineWorld, list[JournalEvent], list[JournalEvent], ScriptedWrapUp]:
    wrap_up = ScriptedWrapUp()
    world = EngineWorld(
        engine_authority=profile_json(engine_profile(max_steps=STEPS)),
        handlers={"lookup": _fetch},
        wrap_up=lambda claim: wrap_up,
    )
    world.journal = w.PipesHubInMemoryJournal(
        clock=world.clock, lease_ms=world.lease_ms, workspaces=world.store,
        engine_authority=world.engine_authority, extension_writers=_writers(), extension_models=_models(),
    )
    parent_model = ScriptedModelPort(ScriptedModel(ModelScript(
        id="wp70",
        turns=[
            Turn(reply=Reply(tool_calls=[ToolCallSpec(name="code_task", args={"goal": _goal(), "task_id": "onyx"})])),
            Turn(reply=Reply(text="(the parent's model is not asked: the child's report is the answer)")),
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
    world.journal.admit(PARENT, org_id=w.ORG, session_id=SESSION, spec=deep_spec(world.engine_authority), input_text=REQUEST)

    async def attempt(run_id: str) -> Any:  # noqa: ANN401
        claim = await world.journal.claim(run_id, worker_id="w-wp70")
        assert claim is not None, run_id
        return await driver.run_claimed(claim, claim_sent_at=world.clock.suspend_aware())

    assert (await attempt(PARENT)).status == "suspended"
    (child,) = world.journal.children_of(PARENT)
    assert (await attempt(child)).status == "completed"
    assert (await attempt(PARENT)).status == "completed"
    return world, list(world.journal.events(SESSION)), list(world.journal.events(child)), wrap_up


async def _text(world: EngineWorld, event: JournalEvent) -> str:
    content = PipesHubContentStore(world.journal.content)
    return (await content.get(ContentRef.model_validate(event.data["textRef"]))).decode("utf-8")


async def test_wp70_e2e_research_cut_at_its_step_allowance_still_delivers_a_cited_report() -> None:
    world, events, child_events, wrap_up = await _run()
    # The child was cut at its step allowance and still answered.
    completed = [e.data for e in child_events if e.type == "run.completed"]
    assert completed == [{"outcome": "answered", "truncatedBy": "turns"}]
    # It was told to stop gathering before the end: the reads after the window opened never ran.
    looked_up = sum(n for (tool, _), n in world.effects.items() if tool == "lookup")
    assert looked_up < PAGES

    # report.md in the conversation's files is the cited report, not the placeholder.
    scope = session_scope(w.ORG, SESSION)
    head = await world.store.head(scope, session_workspace_id(SESSION))
    entry = head.files["out/report.md"]
    report = (await world.objects.get(scope, entry.content)).decode("utf-8")
    assert "Research in progress" not in report
    cited = set(re.findall(r"\(https://docs\.onyx\.app/page-\d+\)", report))
    assert len(cited) == looked_up, "every page it read is cited in the report"
    # Its notes reached the writer.
    assert "Onyx lists its connectors" in wrap_up.prompts[0]

    # The report is published final, a new version over the placeholder.
    stored = await world.artifacts.find_by_name(RunBinding(w.ORG, SESSION, world.journal.children_of(PARENT)[0]), "report.md")
    assert stored is not None and stored.lifecycle == "final" and stored.version >= 2

    # The parent's answer names report.md and cites what it read.
    (final,) = [e for e in events if e.run_id == PARENT and e.type == "message.completed" and e.data.get("isFinal")]
    prose = await _text(world, final)
    # WP-89: the parent shows the engine's prose and relays its outcome, whose words name the file.
    (settled,) = [e for e in events if e.run_id == PARENT and e.type == "x.pipeshub.outcome.settled"]
    answer = f"{prose}\n\n{settled.data['presented']['closing']}"  # type: ignore[index]
    assert settled.data["outcome"]["delivered"]["file"]["path"] == "out/report.md"  # type: ignore[index]
    assert "report.md" in answer
    assert re.search(r"\]\((ref\d+|https://docs\.onyx\.app/page-\d+)\)", answer), answer
    assert "focusing on demonstrated differences" not in answer

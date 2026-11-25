"""ENGCITE2: a code_task engine child's web citations reach the parent's answer as citations.

Found in a manual test ("who won ipl 2026", agent mode): the chat loop handed the work to a claude_code child (code_task,
upfront, research). The child web-searched and answered ``… [Official IPL match report](https://www.iplt20.com/…)``;
the parent passed that on and its saved answer had ``"citations": []``. The child's citation sources lived in the
child's own tool state and were never adopted by the parent, and the child's text was handed over raw, so whatever
form it cited by (``refN`` of its own mapper, ``https://refN.xyz``, the real URL) named nothing the parent's answer
pipeline holds. A spawn_agent child has always handed its sources over (``DeepCitations``); a code_task child now does
the same, with its answer's citations rewritten to its refs first.

The parent is the real ``PipesHubRuntimeAssembler`` (the chat loop, its model scripted); the child runs on the engine
kernel with ``EngineWorld``'s scripted engine, its citation state built by the engine run's own web tool (engine
style, ENGCITE) and its result document shaped by the production child-result shaper.
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
from harness_core.engines.durable.kernel import ENGINE
from harness_core.engines.profiles import profile_json
from harness_core.journal.memory import InMemoryLiveSink
from harness_core.testing import ModelScript, Reply, ScriptedModel, ToolCallSpec, Turn
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.durable import EngineWorld, engine_profile
from harness_core.workspace.forks import StoreChildWorkspaces
from tests.unit.harness.assembly import world as w
from tests.unit.harness.engines.r7_rig import PARENT, SharedContentAssembler, deep_spec

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.engines.durable.ports import AssembledEngineRun, EngineAttempt
    from harness_core.journal.types import Claim, JournalEvent, LiveFrame

PAGE = "https://www.iplt20.com/news/article/tata-ipl-2026-final-rcb-v-gt-match-report"
SNIPPET = "Royal Challengers Bengaluru beat Gujarat Titans in the TATA IPL 2026 final to defend their title."
OTHER = "https://www.espncricinfo.com/series/ipl-2026/final"
OTHER_SNIPPET = "RCB retained the IPL trophy with a five-wicket win over GT in Ahmedabad."
SAID = "Royal Challengers Bengaluru won IPL 2026, beating Gujarat Titans in the final to defend their title."


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


class _Args(BaseModel):
    query: str


async def _searched_child_context() -> AgentContext:
    """The child engine run's tool state after its web_search, as the run's own web tool leaves it."""
    context = AgentContext(
        org_id=w.ORG, user_id="user-1", user_email="user@example.com",
        user_info={"userId": "user-1", "orgId": w.ORG}, org_info={"name": "TestOrg"},
        logger=MagicMock(), llm=MagicMock(), retrieval_service=MagicMock(), graph_provider=MagicMock(),
        config_service=MagicMock(), blob_store=MagicMock(),
    )
    context.tool_state["citation_ref_mapper"] = CitationRefMapper()
    present_for_engines(context)
    payload = {
        "ok": True,
        "result_type": "web_search",
        "query": "who won ipl 2026",
        "web_results": [
            {"title": "TATA IPL 2026 Final: RCB v GT match report", "link": PAGE, "snippet": SNIPPET},
            {"title": "IPL 2026 final", "link": OTHER, "snippet": OTHER_SNIPPET},
        ],
    }

    async def _run(**_kwargs: Any) -> Any:  # noqa: ANN401
        return payload

    tool = StructuredTool.from_function(name="web_search", description="web", args_schema=_Args, coroutine=_run)
    output = await WebToolAdapter(tool, "web", "web_search", context).execute(query="who won ipl 2026")
    context.tool_state["engcite2_read"] = [str(output.data)]
    return context


class _WebSearchingChild(SharedContentAssembler):
    """The engine child: its run's citation state is what its web search left; its result is the production shape."""

    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        built = await super().build(attempt)
        context = await _searched_child_context()
        return dataclasses.replace(
            built, child_result=EngineChildResult(context, lambda: context.tool_state["engcite2_read"])
        )


@dataclasses.dataclass
class _ChatClaims:
    """Node's claim for the parent: a chat-loop (agent mode) run that may hand off to an engine."""

    inner: PipesHubRuntimeAssembler

    async def build(self, attempt: Claim) -> Any:  # noqa: ANN401
        node = w.claim(attempt.spec, query=attempt.input_text, run_id=attempt.run_id, chat_mode="agent")
        values = {f.name: getattr(attempt, f.name) for f in dataclasses.fields(attempt)}
        return await self.inner.build(type(node)(**values, run=node.run))


def _parent(goal: str, answer: str) -> ScriptedModelPort:
    """The chat loop's model: hands the research to code_task, then gives its answer as the guidance asks."""
    calls = [ToolCallSpec(name="code_task", args={"goal": goal, "task_id": "ipl"})]
    return ScriptedModelPort(ScriptedModel(ModelScript(
        id="engcite2", turns=[Turn(reply=Reply(tool_calls=calls)), Turn(reply=Reply(text=answer))],
    )))


@dataclasses.dataclass
class _Ran:
    events: list[JournalEvent]
    outcome: str
    """The code_task result the parent's model read back."""
    frames: list[LiveFrame]
    """The parent's live frames, as its live sink (CITESTREAM) annotated them."""


async def _run(child_answer: str, parent_answer: str, *, query: str = "who won ipl 2026") -> _Ran:
    """The manual test's shape end to end, and the code_task result the parent's model read back."""
    world = EngineWorld(engine_authority=profile_json(engine_profile(max_steps=40)))
    live = InMemoryLiveSink()
    world.journal = w.PipesHubInMemoryJournal(
        clock=world.clock, lease_ms=world.lease_ms, workspaces=world.store,
        engine_authority=world.engine_authority,
        extension_writers=_writers(), extension_models=_models(),
    )
    driver = RunDriver(
        journal=world.journal,
        assembler=_ChatClaims(PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(_parent(f"say {child_answer}", parent_answer)))),
        clock=world.clock,
        children=world.journal,
        child_workspaces=StoreChildWorkspaces(lambda _token: (world.store, world.writers)),
        interactions=world.wakeup,
        kernels={ENGINE: world.kernel(assembler=_WebSearchingChild(world))},
        live=live,
    )
    world.journal.admit(PARENT, org_id=w.ORG, session_id="ses-1", spec=deep_spec(world.engine_authority), input_text=query)

    async def attempt(run_id: str) -> Any:  # noqa: ANN401
        claim = await world.journal.claim(run_id, worker_id="w-engcite2")
        assert claim is not None, run_id
        return await driver.run_claimed(claim, claim_sent_at=world.clock.suspend_aware())

    assert (await attempt(PARENT)).status == "suspended"
    (child,) = world.journal.children_of(PARENT)
    done_child = await attempt(child)
    assert done_child.status == "completed", done_child
    done = await attempt(PARENT)
    assert done.status == "completed", done
    events = list(world.journal.events("ses-1"))
    (outcome,) = [e for e in events if e.type == "tool.outcome" and e.run_id == PARENT]
    read = await PipesHubContentStore(world.journal.content).get(ContentRef.model_validate(outcome.data["resultRef"]))
    return _Ran(events, read.decode("utf-8"), [f for f in live.frames if f.run_id == PARENT])


def _writers() -> dict[str, frozenset[str]]:
    from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_WRITERS
    from harness_core.testing.engines.durable import PROGRESS

    return {**EVENT_WRITERS, PROGRESS: frozenset({"worker"})}


def _models() -> dict[str, Any]:
    from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS

    return dict(EVENT_MODELS)


def _final(events: list[JournalEvent]) -> dict[str, Any]:
    answers = [e.data for e in events if e.type == "x.pipeshub.answer.finalized" and e.run_id == PARENT]
    assert answers, [e.type for e in events if e.run_id == PARENT]
    return dict(answers[-1])


def _web_citations(citations: list[dict[str, Any]]) -> list[str]:
    return [c["metadata"]["webUrl"] for c in citations if c.get("citationType") == "web|url"]


CHILD_FORMS = {
    "real url": f"{SAID} [Official IPL match report]({PAGE})",
    "citation id": f"{SAID} [Official IPL match report](ref1)",
    "old alias": f"{SAID} [Official IPL match report](https://ref1.xyz)",
}


@pytest.mark.parametrize("form", list(CHILD_FORMS))
async def test_engcite2_01_a_code_task_childs_web_citation_is_a_citation_of_the_parents_answer(form: str) -> None:
    """The parent reads the child's answer with the child's citation as its own ref and repeats it, as
    CODE_TASK_GUIDANCE asks; its saved answer cites the page's text fragment, as a native web citation does."""
    adopted = f"{SAID} [Official IPL match report](ref1)"
    ran = await _run(CHILD_FORMS[form], adopted)
    assert "[Official IPL match report](ref1)" in ran.outcome, ran.outcome
    final = _final(ran.events)
    urls = _web_citations(list(final.get("citations") or []))
    assert len(urls) == 1, final
    assert urls[0].startswith(PAGE + "#:~:text="), urls
    answer = str(final.get("answer"))
    # As the chat loop shows a native web citation: the label as text, one numbered chip, no raw link.
    assert "Official IPL match report [1](" in answer, answer
    assert f"]({PAGE})" not in answer and "ref1" not in answer, answer
    streamed = [f.data for f in ran.frames if f.kind == "text_delta" and f.data.get("citations")]
    assert streamed, "the stream numbers the citation while the answer streams"
    assert _web_citations(streamed[-1]["citations"]) == urls
    assert "Official IPL match report [1](" in str(streamed[-1]["answer"])
    recorded = [e for e in ran.events if e.type == "x.pipeshub.citations.recorded" and e.run_id == PARENT]
    assert any(PAGE in json.dumps(e.data) for e in recorded), "the parent journals the sources it adopted"


async def test_engcite2_02_a_citation_the_child_never_read_is_not_handed_on() -> None:
    ran = await _run(f"{SAID} [made up](ref42)", SAID)
    assert "ref42" not in ran.outcome
    assert _web_citations(list(_final(ran.events).get("citations") or [])) == []


ROUTED = "Research who won IPL 2026 and write a comparison report of the finalists"


@pytest.mark.parametrize("form", list(CHILD_FORMS))
async def test_engcite2_03_an_upfront_hand_off_presents_the_childs_answer_with_its_citation(form: str) -> None:
    """The manual test's path: the loop routes the request to the engine up front, and the child's answer is the turn's answer
    as it came back (the director presents it; the parent's model does not rewrite it)."""
    ran = await _run(CHILD_FORMS[form], "the parent's model is not asked again", query=ROUTED)
    [handoff] = [e.data for e in ran.events if e.type == "x.pipeshub.run.handoff" and e.run_id == PARENT]
    assert (handoff["trigger"], handoff["reason"]) == ("upfront", "research")
    final = _final(ran.events)
    assert SAID in str(final.get("answer")), final
    urls = _web_citations(list(final.get("citations") or []))
    assert len(urls) == 1 and urls[0].startswith(PAGE + "#:~:text="), final
    assert "Official IPL match report [1](" in str(final.get("answer"))
    streamed = [f.data for f in ran.frames if f.kind == "text_delta" and f.data.get("citations")]
    assert not streamed or _web_citations(streamed[-1]["citations"]) == urls

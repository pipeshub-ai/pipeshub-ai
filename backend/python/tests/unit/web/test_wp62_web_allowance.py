"""WP-62 (WP62-WEB-1..5): a coding engine run's web allowance is the run's, not a chat answer's.

Found in a manual test: a multi-source research task handed to Codex through ``code_task`` got
the chat-sized allowance (10 searches, 20 page reads, deep research off). Fetches 33 to 38 came back
``budget_exhausted`` with "answer with what you have read" plus the fetch tool's generic hint "fetch those URLs next",
while the run had USD 4.66 of its USD 5 left. One GitHub tree listing it fetched was 4.2 MB of text.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.web_tool_adapter import WebToolAdapter
from app.harness.engines.durable import present_for_engines
from app.utils.chat_helpers import CitationRefMapper
from app.utils.html_to_blocks import TextBlock
from app.web.budget import RUN, STANDARD, WEB_BUDGET_KEY, WebBudget
from app.web.chain import ChainOutcome, Cooldowns
from app.web.errors import BudgetExhausted
from app.web.models import Attempt, CallerContext, WebPage
from app.web.services import WebFetchService
from app.web.tools.fetch_url import MAX_PAGE_TEXT_CHARS, create_fetch_url_tool
from tests.unit.web.test_services import RecordingGate

if TYPE_CHECKING:
    from app.web.deadline import Deadline

CALLER = CallerContext(org_id="org-1", user_id="user-1")
# The recorded child made 26 page reads and 6 searches before it was stopped; a research task needs more than that.
RECORDED_FETCHES = 26


def _context() -> AgentContext:
    context = AgentContext(
        org_id="org-1",
        user_id="user-1",
        user_email="user@example.com",
        user_info={"userId": "user-1", "orgId": "org-1"},
        org_info={"name": "TestOrg"},
        logger=MagicMock(),
        llm=MagicMock(),
        retrieval_service=MagicMock(),
        graph_provider=MagicMock(),
        config_service=MagicMock(),
        blob_store=MagicMock(),
    )
    context.tool_state["citation_ref_mapper"] = CitationRefMapper()
    return context


class _Fetcher:
    id = cooldown_key = "f"

    async def fetch(self, url: str, deadline: Deadline, *, allow_hop: Any = None) -> WebPage:  # noqa: ANN401
        return WebPage(url=url, title="", blocks=(TextBlock(content="text"),), fetched_by="f")


def test_wp62_web_1_an_engine_run_draws_on_a_research_sized_run_allowance() -> None:
    chat, engine = _context(), _context()
    present_for_engines(engine)
    assert chat.web_scope == "answer"
    assert engine.web_scope == "run"
    run = WebBudget.for_context(scope=engine.web_scope, deep_research=False)
    answer = WebBudget.for_context(scope=chat.web_scope, deep_research=False)
    assert (answer.searches, answer.pages) == STANDARD
    assert (run.searches, run.pages, run.scope) == (*RUN, "run")
    # A backstop well past what a research task needs, never the chat count.
    assert RUN[0] >= 5 * STANDARD[0] and RUN[1] >= 10 * STANDARD[1]


async def test_wp62_web_1_the_recorded_research_run_reads_every_page_it_asked_for() -> None:
    """The recorded failure, reproduced on the chat allowance and gone on the run's."""
    chat = WebFetchService([_Fetcher()], RecordingGate(), cooldowns=Cooldowns(), budget=WebBudget.for_answer(deep_research=False))
    refused = 0
    for n in range(RECORDED_FETCHES):
        try:
            await chat.fetch(f"https://a.example/{n}", CALLER)
        except BudgetExhausted:
            refused += 1
    assert refused == RECORDED_FETCHES - STANDARD[1] == 6

    run = WebFetchService([_Fetcher()], RecordingGate(), cooldowns=Cooldowns(), budget=WebBudget.for_run())
    for n in range(RECORDED_FETCHES * 4):
        await run.fetch(f"https://a.example/{n}", CALLER)


def test_wp62_web_2_a_spent_allowance_says_what_is_left_and_to_finish() -> None:
    budget = WebBudget(searches=3, pages=1, scope="run")
    budget.take_page()
    with pytest.raises(BudgetExhausted) as info:
        budget.take_page()
    payload = info.value.to_tool_payload()
    assert payload["error_type"] == "budget_exhausted"
    assert payload["retryable"] is False
    assert payload["left"] == {"searches": 3, "pages": 0}
    assert "This task's page reads are used up (all 1 of them)" in payload["error"]
    assert "Do not call this tool again in this task" in payload["hint"]
    assert "3 web searches are left" in payload["hint"]
    assert "Finish the work from what you have already gathered" in payload["hint"]


def test_wp62_web_2_a_chat_answers_wording_is_unchanged() -> None:
    budget = WebBudget(searches=0, pages=0)
    with pytest.raises(BudgetExhausted) as info:
        budget.take_page()
    assert info.value.message == "The page reads for this answer are used up; answer with what you have read"


async def test_wp62_web_2_the_fetch_tool_does_not_tell_a_spent_run_to_fetch_more() -> None:
    """The recorded refusals carried the generic hint "fetch those URLs next", contradicting the error."""
    service = MagicMock()
    error = BudgetExhausted("used up", left={"searches": 0, "pages": 0}, hint="Do not call this tool again in this task")
    service.fetch = AsyncMock(side_effect=error)
    payload = json.loads(await create_fetch_url_tool(service, CALLER).ainvoke({"url": "https://example.com"}))
    assert payload["hint"] == "Do not call this tool again in this task"
    assert payload["left"] == {"searches": 0, "pages": 0}


class _Args(BaseModel):
    url: str


def _tool(payload: dict[str, Any]) -> StructuredTool:
    async def _run(**_kwargs: Any) -> Any:  # noqa: ANN401
        return payload

    return StructuredTool.from_function(name="fetch_url", description="fetch", args_schema=_Args, coroutine=_run)


_PAGE = {
    "ok": True,
    "result_type": "url_content",
    "url": "https://docs.example.com/a",
    "blocks": [{"type": "text", "content": "PipesHub connects your apps and indexes them."}],
}


async def test_wp62_web_3_an_engines_result_ends_with_the_allowance_left() -> None:
    context = _context()
    present_for_engines(context)
    budget = WebBudget.for_run()
    budget.take_page()
    context.tool_state[WEB_BUDGET_KEY] = budget
    output = await WebToolAdapter(_tool(_PAGE), "web", "fetch_url", context).execute(url="https://docs.example.com/a")
    assert str(output.data).endswith(f"[web allowance left for this task: {RUN[0]} searches, {RUN[1] - 1} page reads]")


async def test_wp62_web_3_a_chat_answers_result_has_no_allowance_line() -> None:
    context = _context()
    context.tool_state[WEB_BUDGET_KEY] = WebBudget.for_answer(deep_research=False)
    output = await WebToolAdapter(_tool(_PAGE), "web", "fetch_url", context).execute(url="https://docs.example.com/a")
    assert "web allowance" not in str(output.data)


async def test_wp62_web_4_a_huge_page_is_cut_and_the_result_says_how_to_read_the_rest() -> None:
    """A recorded ``api.github.com/.../git/trees/main?recursive=1`` came back as 4.2 MB of text."""
    tree = "x" * 4_202_000
    page = WebPage(
        url="https://api.github.com/repos/o/r/git/trees/main?recursive=1",
        title="",
        blocks=(TextBlock(content=tree[:2_000_000]), TextBlock(content=tree[2_000_000:])),
        fetched_by="builtin",
    )
    service = MagicMock()
    service.fetch = AsyncMock(return_value=ChainOutcome(page, "builtin", (Attempt("builtin", None, 0.1),)))
    payload = json.loads(await create_fetch_url_tool(service, CALLER).ainvoke({"url": page.url}))
    kept = sum(len(b["content"]) for b in payload["blocks"])
    assert kept == MAX_PAGE_TEXT_CHARS
    assert "4,202,000" in payload["truncated"] and "narrower address" in payload["truncated"]

    context = _context()
    present_for_engines(context)
    output = await WebToolAdapter(_tool(payload), "web", "fetch_url", context).execute(url=page.url)
    assert "narrower address" in str(output.data)


async def test_wp62_web_4_a_normal_page_is_untouched() -> None:
    page = WebPage(url="https://e.com/", title="t", blocks=(TextBlock(content="hello"),), fetched_by="builtin")
    service = MagicMock()
    service.fetch = AsyncMock(return_value=ChainOutcome(page, "builtin", (Attempt("builtin", None, 0.1),)))
    payload = json.loads(await create_fetch_url_tool(service, CALLER).ainvoke({"url": page.url}))
    assert "truncated" not in payload
    assert payload["blocks"] == [{"type": "text", "content": "hello"}]

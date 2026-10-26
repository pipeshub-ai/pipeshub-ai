"""Deep research: the agent's capability, carried to the prompt, the turn limit and a web budget."""

from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.chat_modes.bridge import _apply_policy_to_chat_state
from app.agents.chat_modes.policy import AgentCapabilities, resolve_agent_policy
from app.web.budget import DEEP_RESEARCH, STANDARD, WebBudget
from app.web.chain import Cooldowns
from app.web.deadline import Deadline
from app.web.errors import AccessDenied, BudgetExhausted
from app.web.models import CallerContext, WebPage, WebResult
from app.web.services import WebFetchService, WebSearchService
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.web.test_services import RecordingGate

CALLER = CallerContext(org_id="org-1", user_id="user-1")
WEB = {"provider": "duckduckgo", "configuration": {}}


class TestPolicy:
    def test_deep_research_needs_web_search(self) -> None:
        assert resolve_agent_policy(AgentCapabilities(deep_search=True)).deep_research is True
        assert resolve_agent_policy(AgentCapabilities(deep_search=True, web_search=False)).deep_research is False
        assert resolve_agent_policy(AgentCapabilities()).deep_research is False

    def test_the_agents_custom_instructions_still_apply(self) -> None:
        assert resolve_agent_policy(AgentCapabilities(deep_search=True)).system_prompt_key == "agent"

    def test_the_chat_state_gets_it_only_with_a_web_provider(self) -> None:
        policy = resolve_agent_policy(AgentCapabilities(deep_search=True))
        state: dict = {}
        _apply_policy_to_chat_state(state, policy, WEB)
        assert state["deep_research"] is True
        _apply_policy_to_chat_state(state, policy, None)
        assert state["deep_research"] is False


class TestContext:
    def test_from_chat_state(self) -> None:
        def context(**state: object) -> AgentContext:
            return AgentContext.from_chat_state({"org_id": "o", "user_id": "u", "logger": MagicMock(), **state})

        assert context(deep_research=True, web_search_config=WEB).deep_research is True
        assert context(deep_research=True, web_search_config=None).deep_research is False
        assert context(web_search_config=WEB).deep_research is False


def build_prompt(context: AgentContext) -> str:
    spec = AgentSpec(name="pipeshub-agent", system_prompt="BASE", tool_names=[], model=ModelSpec(provider="scripted", model="m"))
    with patch.multiple(
        "app.agents.agent_loop.prompt_builder",
        _build_knowledge_context=MagicMock(return_value=""),
        build_llm_time_context=MagicMock(return_value="Current time: 2026-01-01T00:00:00Z"),
        build_capability_summary=MagicMock(return_value=""),
        _format_user_context=MagicMock(return_value=""),
    ):
        return PipesHubPromptBuilder(context).build(spec, AgentRuntime(tool_registry=ToolRegistry()), Goal(description="q"), [], {})


class TestPrompt:
    def test_the_research_method_is_in_the_prompt(self) -> None:
        prompt = build_prompt(make_context(web_search_config=WEB, deep_research=True))
        assert "## Deep Research" in prompt
        assert "at least two independent sources" in prompt

    @pytest.mark.parametrize(("web", "deep"), [(WEB, False), (None, True)])
    def test_absent_otherwise(self, web: dict | None, deep: bool) -> None:
        assert "## Deep Research" not in build_prompt(make_context(web_search_config=web, deep_research=deep))

    def test_it_sits_in_the_cached_part_of_the_prompt(self) -> None:
        from app.agents.agent_loop.section_order import TURN_VOLATILE_NAMES

        assert "deep_research" not in TURN_VOLATILE_NAMES


class TestTurns:
    def test_deep_research_gets_more_turns(self) -> None:
        from app.agents.agent_loop import factory

        assert factory._DEEP_RESEARCH_MAX_TURNS > factory._MAX_TURNS


class TestBudget:
    def test_sizes(self) -> None:
        assert (WebBudget.for_answer(deep_research=False).searches, WebBudget.for_answer(deep_research=False).pages) == STANDARD
        assert (WebBudget.for_answer(deep_research=True).searches, WebBudget.for_answer(deep_research=True).pages) == DEEP_RESEARCH
        assert DEEP_RESEARCH[0] > STANDARD[0] and DEEP_RESEARCH[1] > STANDARD[1]

    def test_spending(self) -> None:
        budget = WebBudget(searches=1, pages=1)
        budget.take_search()
        budget.take_page()
        with pytest.raises(BudgetExhausted):
            budget.take_search()
        with pytest.raises(BudgetExhausted) as info:
            budget.take_page()
        assert info.value.to_tool_payload()["error_type"] == "budget_exhausted"
        assert "answer with what you have" in info.value.message

    async def test_the_search_service_stops_at_the_budget(self) -> None:
        class Provider:
            id = cooldown_key = "p"
            calls = 0

            async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
                Provider.calls += 1
                return [WebResult(f"https://a.example/{query}", query, "s", "p")]

        service = WebSearchService([Provider()], RecordingGate(), cooldowns=Cooldowns(), budget=WebBudget(searches=2, pages=0))
        await service.search("one", CALLER)
        await service.search("two", CALLER)
        with pytest.raises(BudgetExhausted):
            await service.search("three", CALLER)
        assert Provider.calls == 2

    async def test_the_fetch_service_stops_at_the_budget(self) -> None:
        class Fetcher:
            id = cooldown_key = "f"

            async def fetch(self, url: str, deadline: Deadline, *, allow_hop=None) -> WebPage:
                return WebPage(url=url, title="", blocks=(), fetched_by="f")

        service = WebFetchService([Fetcher()], RecordingGate(), cooldowns=Cooldowns(), budget=WebBudget(searches=0, pages=1))
        await service.fetch("https://a.example/", CALLER)
        with pytest.raises(BudgetExhausted):
            await service.fetch("https://b.example/", CALLER)

    async def test_a_refused_request_spends_nothing(self) -> None:
        budget = WebBudget(searches=1, pages=1)
        service = WebFetchService([], RecordingGate(denied={"https://no.example/"}), cooldowns=Cooldowns(), budget=budget)
        with pytest.raises(AccessDenied):
            await service.fetch("https://no.example/", CALLER)
        assert budget.pages == 1

"""Solo chats (no `collaboration`) must be byte-identical to pre-PH-08 output.

The goldens in `fixtures/` were captured from the code before PR-08a and must not be edited.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import _convert_conversation_turn
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.api.routes.agent import ChatQuery as AgentChatQuery
from app.api.routes.chatbot import ChatQuery
from app.modules.agents.collaboration import turns_to_dicts
from app.modules.agents.qna.chat_state import build_initial_state
from tests.unit.agents.adapter.conftest import make_context

FIXTURES = Path(__file__).parent / "fixtures"


def _pipeline(model: type) -> dict:
    payload = json.loads((FIXTURES / "solo_history_input.json").read_text())
    q = model(**payload)
    query_dict = {"query": q.query, "previous_conversations": turns_to_dicts(q.previousConversations)}
    assert "collaboration" not in query_dict and "resume" not in query_dict
    state = build_initial_state(
        chat_query=query_dict, user_info={"orgId": "o", "userId": "u"},
        llm=MagicMock(), logger=MagicMock(), retrieval_service=MagicMock(), graph_provider=MagicMock(),
        reranker_service=MagicMock(), config_service=MagicMock(), model_name="m", model_key="k",
        has_sql_connector=False,
    )
    ctx = AgentContext.from_chat_state(state)
    assert ctx.collaboration is None and ctx.resume is None
    msgs = []
    for t in ctx.previous_conversations:
        msgs.extend(m.model_dump(mode="json") for m in _convert_conversation_turn(t, collaboration=ctx.collaboration))
    return {"previous_conversations": ctx.previous_conversations, "messages": msgs}


def _normalize(obj: object) -> object:
    return json.loads(json.dumps(obj, sort_keys=True, default=str))


def test_chatbot_solo_history_matches_golden() -> None:
    golden = json.loads((FIXTURES / "solo_history_golden.json").read_text())
    assert _normalize(_pipeline(ChatQuery)) == golden


def test_agent_solo_history_matches_golden() -> None:
    golden = json.loads((FIXTURES / "solo_history_golden.json").read_text())
    assert _normalize(_pipeline(AgentChatQuery)) == golden


def _blocks(**kw: object) -> dict[str, str]:
    spec = AgentSpec(name="pipeshub-agent", system_prompt="BASE_REACT_PROMPT", tool_names=[],
                     model=ModelSpec(provider="scripted", model="scripted-model"))
    rt = AgentRuntime(tool_registry=ToolRegistry())
    with patch.multiple(
        "app.agents.agent_loop.prompt_builder",
        _build_knowledge_context=MagicMock(return_value=""),
        build_llm_time_context=MagicMock(return_value=""),
        build_capability_summary=MagicMock(return_value="CAPS"),
        _format_user_context=MagicMock(return_value="USER_CTX"),
    ), patch("app.modules.agents.context.tool_surface.sandbox_network_enabled", MagicMock(return_value=False)):
        s, v = PipesHubPromptBuilder(make_context(**kw)).build_blocks(spec, rt, Goal(description="hello"), [], {})
    return {"stable": s, "volatile": v}


def test_solo_prompt_blocks_match_golden() -> None:
    golden = json.loads((FIXTURES / "solo_prompt_golden.json").read_text())
    got = {"default": _blocks(), "instructions": _blocks(instructions="Always respond in French.", timezone="UTC")}
    assert got == golden

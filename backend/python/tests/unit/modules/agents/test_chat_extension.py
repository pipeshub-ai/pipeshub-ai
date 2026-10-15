"""The default chat hooks (`ChatExtension`) keep a chat that belongs to one user
exactly as it was before the hooks existed."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from app.agents.agent_loop.cancellation.policy import CancelRequester
from app.agents.agent_loop.cancellation.registry import RunOwner
from app.agents.agent_loop.factory import _convert_conversation_turn
from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER, LEGACY_FORMATTER
from app.modules.agents.chat_extension import ChatExtension, default_chat_extension
from app.modules.agents.service import builders
from app.modules.agents.service.agent_service import AgentService
from app.modules.authz.chat_content_access import (
    NoChatContentAccess,
    resolve_read_access,
)
from tests.unit.agents.adapter.conftest import make_context

EXT = ChatExtension()
PREFIX = 'User selections:\n1. "Which?" → A'


class _Query(BaseModel):
    query: str
    previousConversations: list[dict] = []


def test_the_default_is_a_plain_chat_extension() -> None:
    assert type(default_chat_extension()) is ChatExtension


class TestRequestHooks:
    def test_request_model_and_query_fields_are_unchanged(self) -> None:
        assert EXT.chat_query_model(_Query) is _Query
        assert EXT.query_fields(_Query(query="q", previousConversations=[{"role": "user_query"}])) == {}

    async def test_no_preflight_answer(self) -> None:
        assert await EXT.preflight(
            _Query(query="@assistant help"), graph_provider=MagicMock(), config_service=MagicMock(),
            user_id="u", org_id="o", logger=logging.getLogger("test"),
        ) is None

    def test_state_and_context_carry_nothing_extra(self) -> None:
        assert EXT.chat_state_fields({"extra": {"x": 1}, "other": [1]}) == {}
        assert EXT.context_extensions({"extra": {"x": 1}}) == {}
        assert EXT.tool_state_fields({}) == {}


class TestAgentLoopHooks:
    async def test_no_extra_toolsets_and_nothing_skipped(self) -> None:
        assert EXT.toolset_modules() == ()
        assert await EXT.skip_apps(make_context()) == set()
        assert EXT.top_level_only_tool_prefixes() == ()
        assert EXT.transcript_hidden_tool_suffixes() == ()

    def test_resume_answers_bind_to_the_last_real_question(self) -> None:
        prev = [{"role": "user_query", "content": "goal"}, {"role": "bot_response", "content": "Which?"}]
        assert EXT.resolve_resume(PREFIX, make_context(previous_conversations=prev)) == (PREFIX, "goal")
        assert EXT.resolve_resume("hello", make_context(previous_conversations=prev)) == (None, "hello")

    def test_no_hooks_are_installed(self) -> None:
        hooks = MagicMock()
        EXT.install_run_hooks(hooks, make_context(), query="q", resume_answers=None)
        EXT.install_tool_result_hooks(hooks, make_context())
        assert hooks.mock_calls == []

    def test_history_is_rendered_as_sent(self) -> None:
        assert EXT.history_renderer(make_context()) is None
        assert _convert_conversation_turn({"role": "note", "content": "x"}) == []
        (answer,) = _convert_conversation_turn({"role": "bot_response", "content": "hi", "agentRef": "agent:x"})
        assert answer.content[0].text == "hi"

    def test_user_context_stays_in_its_section(self) -> None:
        assert EXT.prompt_sections(make_context(), "USER_CTX") == {"user_context": "USER_CTX"}


class TestAccessHooks:
    async def test_chat_content_needs_an_acl_path(self) -> None:
        assert isinstance(EXT.content_access(), NoChatContentAccess)
        graph = MagicMock()
        graph.check_record_access_with_details = AsyncMock(return_value=None)
        assert not await NoChatContentAccess().can_read_chat_content(
            graph, user_id="u", org_id="o", record="r", conversation_id="c", acl_version=1,
        )
        assert await resolve_read_access(graph, NoChatContentAccess(), user_id="u", org_id="o", record="r") is None

    async def test_an_acl_path_is_enough(self) -> None:
        graph = MagicMock()
        graph.check_record_access_with_details = AsyncMock(return_value={"record": {"id": "r"}})
        details = await resolve_read_access(graph, NoChatContentAccess(), user_id="u", org_id="o", record="r")
        assert details == {"record": {"id": "r"}}

    def test_no_extra_service_scope(self) -> None:
        assert EXT.extra_service_scopes() == frozenset()

    def test_a_cancel_grant_is_refused(self) -> None:
        owner = RunOwner(user_id="a", org_id="o", conversation_id="c")
        grant = CancelRequester(user_id="b", org_id="o", conversation_id="c", via_participant_grant=True)
        assert EXT.may_cancel_by_grant(owner, grant) is False


class TestAgentAndStartupHooks:
    def test_agents_are_created_by_the_shared_service(self) -> None:
        assert EXT.agent_service_class() is AgentService

    async def test_install_and_migrations_do_nothing(self) -> None:
        container = MagicMock()
        EXT.install(container)
        await EXT.run_startup_migrations(MagicMock(), MagicMock(), logging.getLogger("test"))
        assert container.mock_calls == []

    def test_service_account_sharing_comes_from_the_edition(self) -> None:
        from app import edition_services

        assert builders.sa_forces_org_sharing() is edition_services.sa_forces_org_sharing()


@pytest.mark.parametrize("formatter", [LEGACY_FORMATTER, AGUI_FORMATTER], ids=["legacy", "agui"])
def test_custom_event_carries_name_and_value(formatter) -> None:
    (frame,) = formatter.custom_event(make_context(run_id="run-1"), name="card", value={"a": 1})
    if formatter is LEGACY_FORMATTER:
        assert frame == {"event": "card", "data": {"a": 1}}
    else:
        assert frame["event"] == "CUSTOM"
        assert frame["data"]["name"] == "card" and frame["data"]["value"] == {"a": 1}

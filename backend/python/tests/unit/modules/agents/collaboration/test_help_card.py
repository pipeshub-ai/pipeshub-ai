"""MN-20: `@assistant help` is a canned card computed as the sender, with no model call."""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.chatbot import ChatQuery, _generate_chat_stream_via_agent_loop
from app.modules.agents.collaboration.help_card import (
    ASSISTANT_ALIASES,
    build_help_card,
    is_help_command,
)

LOGGER = logging.getLogger("test")
NODE_ALIASES = (
    Path(__file__).resolve().parents[6]
    / "nodejs/apps/src/modules/enterprise_search/services/collaboration/mentions/reserved-aliases.json"
)


class _Graph:
    """Per-user access, like the real provider: the answer depends only on the user asked about."""

    def __init__(self, access: dict[str, list[str]]) -> None:
        self.access = access
        self.calls: list[tuple[str, str]] = []

    async def get_accessible_connector_types(self, user_id: str, org_id: str) -> list[str]:
        self.calls.append((user_id, org_id))
        return list(self.access.get(user_id, []))


def _request(user_id: str) -> MagicMock:
    request = MagicMock()
    request.state.user = {"orgId": "org-1", "userId": user_id}
    request.query_params = {}
    return request


def _collab(n: int) -> dict:
    refs = [f"participant_{i + 1}" for i in range(n)]
    return {
        "participants": [{"ref": r, "displayName": r, "isCurrentSender": i == 0} for i, r in enumerate(refs)],
        "currentSenderRef": refs[0],
    }


async def _run(
    graph: _Graph, user_id: str, query: str = "@assistant help", participants: int = 3, flag: bool = True
) -> tuple[list[str], MagicMock, MagicMock]:
    body = {"query": query, "conversationId": "conv-1", "runId": "0b2f9c1e-6f0a-4a57-9a52-3f3c1d9c8a11"}
    if participants > 1:
        body["collaboration"] = _collab(participants)
    with (
        patch("app.api.routes.chatbot.is_chat_mentions_enabled", new=AsyncMock(return_value=flag)),
        patch("app.api.routes.chatbot.get_llm_for_chat", new=AsyncMock(return_value=None)) as llm,
        patch("app.api.routes.chatbot.run_chat_stream") as run_chat,
    ):
        events = [
            c
            async for c in _generate_chat_stream_via_agent_loop(
                _request(user_id), ChatQuery(**body), AsyncMock(), graph, AsyncMock()
            )
        ]
    return events, llm, run_chat


def _final_answer(events: list[str]) -> dict:
    finished = [e for e in events if e.startswith("event: RUN_FINISHED")]
    assert len(finished) == 1
    return json.loads(finished[0].split("data: ", 1)[1])["result"]


class TestIsHelpCommand:
    @pytest.mark.parametrize("alias", ASSISTANT_ALIASES)
    def test_every_alias_with_help(self, alias) -> None:
        assert is_help_command(f"@{alias} help")
        assert is_help_command(f"@{alias.upper()}  HELP ")

    @pytest.mark.parametrize("text", ["<@assistant:self> help", "@assistant, help", "@assistant help?", "@assistant: help!"])
    def test_variants(self, text) -> None:
        assert is_help_command(text)

    @pytest.mark.parametrize(
        "text",
        [
            "help",
            "@assistant",
            "@assistant helpful summary",
            "@assistant help me summarize this",
            "please @assistant help",
            "@everyone help",
            "@bob help",
            "<@user:bob> help",
            "@assistant\nhelp me",
            "",
            None,
        ],
    )
    def test_anything_else_is_a_normal_message(self, text) -> None:
        assert not is_help_command(text)

    def test_alias_list_matches_the_node_table(self) -> None:
        assert list(ASSISTANT_ALIASES) == json.loads(NODE_ALIASES.read_text())["assistant"]


class TestBuildHelpCard:
    async def test_lists_the_senders_connectors_and_participants(self) -> None:
        graph = _Graph({"u1": ["DRIVE", "SLACK"]})
        card = await build_help_card(graph, user_id="u1", org_id="o", participant_count=3, logger=LOGGER)
        assert card.connectors == ["Google Drive", "Slack"]
        assert "Google Drive, Slack" in card.answer
        assert "3 people" in card.answer
        assert graph.calls == [("u1", "o")]

    async def test_kb_is_shown_as_collections(self) -> None:
        graph = _Graph({"u1": ["KB"]})
        card = await build_help_card(graph, user_id="u1", org_id="o", participant_count=1, logger=LOGGER)
        assert card.connectors == ["Collections"]
        assert "Only you" in card.answer

    async def test_no_connectors_is_stated_not_invented(self) -> None:
        card = await build_help_card(_Graph({}), user_id="u1", org_id="o", participant_count=2, logger=LOGGER)
        assert card.connectors == []
        assert "could not find any connected sources" in card.answer

    async def test_a_failing_lookup_still_answers(self) -> None:
        graph = MagicMock()
        graph.get_accessible_connector_types = AsyncMock(side_effect=RuntimeError("down"))
        card = await build_help_card(graph, user_id="u1", org_id="o", participant_count=2, logger=LOGGER)
        assert card.connectors == []


class TestRouteShortCircuit:
    async def test_no_model_call_and_a_complete_run(self) -> None:
        graph = _Graph({"u1": ["DRIVE"]})
        events, llm, run_chat = await _run(graph, "u1")
        llm.assert_not_called()
        run_chat.assert_not_called()
        assert events[0].startswith("event: RUN_STARTED")
        result = _final_answer(events)
        assert "Google Drive" in result["answer"]
        assert result["citations"] == []

    async def test_card_is_computed_as_the_sender_and_never_leaks_across_users(self) -> None:
        graph = _Graph({"alice": ["DRIVE", "SLACK"], "bob": ["KB"]})
        events_a, _, _ = await _run(graph, "alice")
        events_b, _, _ = await _run(graph, "bob")
        a, b = _final_answer(events_a)["answer"], _final_answer(events_b)["answer"]
        assert "Slack" in a and "Google Drive" in a
        assert "Slack" not in b and "Google Drive" not in b
        assert "Collections" in b and "Collections" not in a
        assert [c[0] for c in graph.calls] == ["alice", "bob"]

    async def test_flag_off_falls_through_to_the_model_path(self) -> None:
        graph = _Graph({"u1": ["DRIVE"]})
        events, llm, _ = await _run(graph, "u1", flag=False)
        llm.assert_called_once()
        assert graph.calls == []
        assert events[0].startswith("event: RUN_ERROR")

    async def test_a_normal_message_never_reads_the_flag_or_the_graph(self) -> None:
        graph = _Graph({"u1": ["DRIVE"]})
        events, llm, _ = await _run(graph, "u1", query="@assistant help me summarize")
        llm.assert_called_once()
        assert graph.calls == []

    async def test_solo_chat_has_no_participant_claim_about_others(self) -> None:
        events, _, _ = await _run(_Graph({"u1": []}), "u1", participants=1)
        assert "Only you" in _final_answer(events)["answer"]

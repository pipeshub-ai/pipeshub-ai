"""Bounded and faithful history (docs/context-engineering/REPORT.md CE-09).

Node sends every saved turn on every request, and every earlier attachment was
re-rendered in full into its replayed question. A 200-turn chat replayed about
1.25 MB, and one 6,000-block PDF on an early turn (about 311K tokens) made every
later request fail with `ContextBudgetExceeded`. History now fits a fixed share
of the message budget (D8: 30%), newest exchanges whole, and attachments render
within a budget with a note saying where they stop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from app.agent_loop_lib.agent.hook_dispatch import dispatch_pre_model
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    TextPart,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_tokens
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks.attachment_resolver import resolve_attachments_for_goal

if TYPE_CHECKING:
    from app.agent_loop_lib.context.manager import ContextManager

CURRENT = "What does the leave policy say about carry-over days?"
WINDOW = 128_000
_WORDS = "practice calendar patient invoice reminder settings audit role clinician export billing claim "


def _text(chars: int) -> str:
    return (_WORDS * (chars // len(_WORDS) + 1))[:chars]


class _FakeAgent:
    def __init__(self) -> None:
        self._context: ContextManager | None = None

    @property
    def context(self) -> ContextManager | None:
        return self._context

    def seed_context(self, context: ContextManager) -> None:
        self._context = context


def _readable_graph() -> MagicMock:
    graph = MagicMock()
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    graph.check_record_access_with_details = AsyncMock(return_value={"id": "rec-1"})
    return graph


def _big_record(n_blocks: int = 6_000) -> dict[str, Any]:
    return {
        "id": "rec-1", "virtual_record_id": "vr1", "record_name": "handbook.pdf", "org_id": "org",
        "block_containers": {
            "blocks": [
                {"index": i, "type": "text", "data": f"Paragraph {i}: " + "lorem ipsum dolor " * 10}
                for i in range(n_blocks)
            ],
            "block_groups": [],
        },
    }


def _context(**fields: Any) -> AgentContext:  # noqa: ANN401
    fields.setdefault("configured_context_window", WINDOW)
    return AgentContext(org_id="org", user_id="user", user_email="user@example.com", **fields)


def _blob(record: dict[str, Any]) -> MagicMock:
    blob = MagicMock()
    blob.get_record_from_storage = AsyncMock(return_value=record)
    return blob


def _turns(count: int) -> list[dict[str, Any]]:
    """`count` exchanges of a question and an answer that used two tools."""
    rows: list[dict[str, Any]] = []
    for t in range(count):
        rows.append({"role": "user_query", "content": f"QUESTION {t}: how do SMS appointment reminders work?"})
        rows.append({
            "role": "bot_response",
            "content": f"ANSWER {t}: enable reminders in Practice Settings. " + _text(1_500),
            "tool_results": [
                {"tool_name": "knowledgegraph__search", "tool_id": f"h{t}_{i}", "result": _text(800),
                 "args": {"query": "sms reminders"}, "status": "success"}
                for i in range(2)
            ],
        })
    return rows


def _message_budget() -> int:
    return ContextBudget.for_model(None, context_window=WINDOW).effective_max_tokens


async def _seed(rows: list[dict[str, Any]], context: AgentContext | None = None) -> list:
    agent = _FakeAgent()
    await PipesHubAgentFactory._seed_conversation_history(agent, rows, context or _context())
    return await agent.context.messages()


def _texts(messages: list) -> list[str]:
    out = []
    for message in messages:
        if isinstance(message, UserMessage):
            content = message.content
            out.append(content if isinstance(content, str) else "".join(
                p.text for p in content if isinstance(p, TextPart)))
        else:
            out.append(message.text)
    return out


class TestHistoryFitsItsShare:
    """HG-01."""

    async def test_two_hundred_turns_fit_the_history_share(self) -> None:
        seeded = await _seed(_turns(200))

        assert count_tokens(seeded) <= int(_message_budget() * 0.30)

    async def test_the_newest_turns_are_kept_whole(self) -> None:
        seeded = await _seed(_turns(200))

        texts = _texts(seeded)
        last_question = max(i for i, t in enumerate(texts) if t.startswith("QUESTION 199"))
        tail = seeded[last_question:]
        assert [type(m).__name__ for m in tail] == [
            "UserMessage", "AssistantMessage", "ToolMessage", "ToolMessage", "AssistantMessage",
        ]
        assert tail[-1].text.startswith("ANSWER 199") and len(tail[-1].text) > 1_500

    async def test_older_turns_are_reduced_to_question_answer_opening_and_tool_names(self) -> None:
        seeded = await _seed(_turns(200))

        reduced = [
            m for m in seeded
            if isinstance(m, AssistantMessage) and not m.tool_calls and "(Tools used:" in m.text
        ]
        assert reduced, "the oldest turns must be reduced"
        for answer in reduced:
            assert answer.text.startswith("ANSWER ")
            assert len(answer.text) < 1_000
            assert answer.text.endswith("(Tools used: knowledgegraph__search.)")
        assert not any(isinstance(m, ToolMessage) for m in seeded[: seeded.index(reduced[-1])])

    async def test_turns_that_do_not_fit_even_reduced_are_counted_in_a_note(self) -> None:
        seeded = await _seed(_turns(200))

        first = seeded[0]
        assert isinstance(first, UserMessage) and first.injected
        assert "earlier turns of this conversation are not shown" in first.content

    async def test_the_window_is_deterministic(self) -> None:
        first = await _seed(_turns(200))
        second = await _seed(_turns(200))

        assert [m.model_dump() for m in first] == [m.model_dump() for m in second]

    async def test_a_short_history_is_replayed_whole(self) -> None:
        seeded = await _seed(_turns(3))

        assert sum(isinstance(m, ToolMessage) for m in seeded) == 6
        assert not any(isinstance(m, UserMessage) and m.injected for m in seeded)

    async def test_seeding_is_logged_with_counts_only(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.factory"):
            await _seed(_turns(200), _context(conversation_id="conv-1", run_id="run-1"))

        line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("history_seeded"))
        assert "run_id=run-1" in line and "conversation_id=conv-1" in line
        assert "whole=" in line and "reduced=" in line and "omitted=" in line
        assert "QUESTION" not in line and "ANSWER" not in line


class TestALargeEarlierAttachment:
    """SC-10: a 6,000-block PDF attached on an early turn."""

    def _rows(self) -> list[dict[str, Any]]:
        return [
            {"role": "user_query", "content": "Summarise this handbook",
             "attachments": [{"recordName": "handbook.pdf", "mimeType": "application/pdf", "virtualRecordId": "vr1"}]},
            {"role": "bot_response", "content": "The handbook covers leave, travel and expenses."},
        ]

    def _context(self) -> AgentContext:
        return _context(blob_store=_blob(_big_record()), graph_provider=_readable_graph())

    async def test_it_renders_within_the_history_share_with_a_note(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            seeded = await _seed(self._rows(), self._context())

        assert count_tokens(seeded) <= int(_message_budget() * 0.30)
        question = _texts(seeded)[0]
        assert question.startswith("Summarise this handbook")
        assert "knowledgegraph__fetch_record" in question and "start_block=" in question
        assert any(r.getMessage().startswith("attachment_truncated") for r in caplog.records)
        assert not any("handbook" in r.getMessage() for r in caplog.records)

    async def test_the_production_chain_sends_the_current_request_without_exceeding(self) -> None:
        context = self._context()
        seeded = await _seed(self._rows(), context)
        hooks = PipesHubAgentFactory._build_hooks(
            context, transport_registry=MagicMock(), model_name="m",
        )
        budget = ContextBudget.for_model("m", context_window=WINDOW, reserved_prompt_tokens=12_000)
        stored = [*seeded, UserMessage(content=CURRENT)]

        sent = await dispatch_pre_model(hooks, stored, budget, turn_index=0, max_turns=30)

        humans = [m for m in sent if isinstance(m, UserMessage) and not m.injected]
        assert humans[-1].content == CURRENT
        assert count_tokens(sent) <= budget.hard_limit_tokens
        # The history fits its share, so the first call needs no lossy compaction of it.
        assert [m.model_dump() for m in sent[: len(seeded)]] == [m.model_dump() for m in seeded]

    async def test_an_old_turn_does_not_replay_its_attachment(self) -> None:
        rows = [*self._rows(), *_turns(200)]
        seeded = await _seed(rows, self._context())

        joined = "\n".join(_texts(seeded))
        assert "Paragraph 0:" not in joined
        assert count_tokens(seeded) <= int(_message_budget() * 0.30)


class TestALargeCurrentAttachment:
    """The current turn's attachment is inlined into the current request, which
    no compaction layer shortens, so it is bounded where it is rendered."""

    def _context(self) -> AgentContext:
        return _context(
            blob_store=_blob(_big_record()), graph_provider=_readable_graph(),
            tool_state={"attachments": [
                {"recordName": "handbook.pdf", "mimeType": "application/pdf", "virtualRecordId": "vr1"},
            ]},
        )

    async def test_it_is_bounded_with_a_note(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            text, _ = await resolve_attachments_for_goal(self._context(), logging.getLogger("t"))

        # The share bounds the record; the list header and the attachable-ids table come with it.
        assert len(text) <= int(_message_budget() * 0.40) * 4 + 1_000
        assert "Paragraph 0:" in text
        assert "knowledgegraph__fetch_record" in text and "start_block=" in text
        assert any(r.getMessage().startswith("attachment_truncated") for r in caplog.records)

    async def test_the_production_chain_sends_it_without_exceeding(self) -> None:
        context = self._context()
        text, _ = await resolve_attachments_for_goal(context, logging.getLogger("t"))
        hooks = PipesHubAgentFactory._build_hooks(
            context, transport_registry=MagicMock(), model_name="m",
        )
        budget = ContextBudget.for_model("m", context_window=WINDOW, reserved_prompt_tokens=12_000)
        request = f"{CURRENT}\n\n{text}"

        sent = await dispatch_pre_model(hooks, [UserMessage(content=request)], budget, turn_index=0, max_turns=30)

        assert sent[-1].content == request
        assert count_tokens(sent) <= budget.effective_max_tokens

    async def test_a_small_attachment_is_inlined_whole(self) -> None:
        context = self._context()
        context.blob_store = _blob(_big_record(n_blocks=20))

        text, _ = await resolve_attachments_for_goal(context, logging.getLogger("t"))

        assert "Paragraph 19:" in text
        assert "start_block=" not in text


class TestTheIntentHistoryView:
    """The intent call replays earlier attachments too; they are bounded the
    same way as the agent's."""

    async def test_an_earlier_large_attachment_is_cut_with_a_note(self) -> None:
        from app.modules.agents.qna.router import build_prior_routing_messages

        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(return_value=_big_record())
        rows = TestALargeEarlierAttachment()._rows()

        messages = await build_prior_routing_messages(
            {"previous_conversations": rows}, blob_store=blob, org_id="org",
            user_id="user", graph_provider=_readable_graph(),
        )

        texts = [p["text"] for p in messages[0].content if p.get("type") == "text"]
        bound = int(ContextBudget.for_model(None).effective_max_tokens * 0.30 * 0.5) * 4
        assert sum(len(t) for t in texts) <= bound + 1_000
        assert texts[-1] == "[… the rest of this attachment is not shown here]"

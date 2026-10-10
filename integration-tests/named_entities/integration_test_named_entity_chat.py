"""Named entities in chat: the agent's entity tools return the right records, the
request's entity filter scopes the answer, and the ordinary knowledge search
still answers as it did.

Each turn's tool calls are read back from the stream, so the assertions are on
what each tool was asked and what it returned, not only on the final answer.
The prompts name the tool to use: which tool a model picks unprompted is a
model question, and these tests are about what the tools do.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ai_agents.support import delete_conversation, stream_chat
from helper.access_probe import _cited_ids

pytestmark = [pytest.mark.integration, pytest.mark.ai_agents, pytest.mark.named_entities]

REFUND = {"amount": {"min": -800, "max": -700, "currency": "USD"}}


def _turn(conversations_client, corpus, query: str, *, mode: str = "agent", entity_filters: dict | None = None):
    filters: dict[str, Any] = {"kb": [corpus.kb_id]}
    if entity_filters:
        filters["entityFilters"] = entity_filters
    trace = stream_chat(conversations_client, {"query": query, "chatMode": mode, "filters": filters})
    assert trace.finished and not trace.error, trace.describe()
    return trace


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


@pytest.fixture(scope="module")
def value_turn(conversations_client, ner_corpus):
    trace = _turn(conversations_client, ner_corpus, (
        "Use the find_records_by_value tool to find the documents that mention an amount "
        "between -800 and -700 USD, then tell me each document's title."
    ))
    yield trace
    delete_conversation(conversations_client, trace)


@pytest.fixture(scope="module")
def entity_turn(conversations_client, ner_corpus):
    trace = _turn(conversations_client, ner_corpus, (
        "Use the search_entities tool to find the organization Globex, then the "
        "find_records_by_entity tool to list the documents that mention it."
    ))
    yield trace
    delete_conversation(conversations_client, trace)


@pytest.mark.asyncio(loop_scope="session")
class TestEntityToolsInChat:
    async def test_the_value_tool_is_asked_for_the_range_and_returns_the_document(self, value_turn, ner_corpus) -> None:
        calls = value_turn.calls_ending_with("find_records_by_value")
        assert calls, value_turn.describe()
        args = _json(calls[0].args) or {}
        assert args.get("amount_min") is not None and args.get("amount_max") is not None, calls[0].args
        assert -800 <= float(args["amount_min"]) <= float(args["amount_max"]) <= -700 or (
            float(args["amount_min"]) <= -742 <= float(args["amount_max"])
        ), calls[0].args
        returned = [call.result for call in calls]
        assert any(ner_corpus.record_ids["settlement"] in result for result in returned), returned
        assert not any(ner_corpus.record_ids["contract"] in result for result in returned), returned

    async def test_the_answer_names_the_document_the_tool_found(self, value_turn) -> None:
        answer = value_turn.answer.casefold()
        assert "settlement" in answer or "siemens" in answer, value_turn.describe()

    async def test_the_entity_tools_resolve_a_name_to_the_documents_that_mention_it(self, entity_turn, ner_corpus) -> None:
        searches = entity_turn.calls_ending_with("search_entities")
        assert searches and any("globex" in call.result.casefold() for call in searches), entity_turn.describe()
        records = entity_turn.calls_ending_with("find_records_by_entity")
        assert records, entity_turn.describe()
        returned = " ".join(call.result for call in records)
        assert ner_corpus.record_ids["contract"] in returned, returned[:600]
        assert ner_corpus.record_ids["settlement"] not in returned, returned[:600]


@pytest.mark.asyncio(loop_scope="session")
class TestTheKnowledgeSearchStillAnswers:
    async def test_an_ordinary_question_is_answered_from_the_document_with_citations(
        self, conversations_client, ner_corpus,
    ) -> None:
        trace = _turn(conversations_client, ner_corpus, "What is the annual fee in the master services agreement?")
        try:
            answer = trace.answer.replace(",", "")
            assert "1250000" in answer or "1.25 million" in answer.casefold(), trace.describe()
            # A chat citation names its record (metadata.recordId), not the virtual record.
            cited, _virtual = _cited_ids(trace.citations)
            assert ner_corpus.record_ids["contract"] in cited, trace.describe()
        finally:
            delete_conversation(conversations_client, trace)


_CONTENT_SEARCHES = ("search_internal_knowledge", "knowledgegraph__search")


@pytest.mark.asyncio(loop_scope="session")
class TestTheRequestEntityFilterScopesTheAnswer:
    """In chat the request's entity filter scopes every content search. Browsing
    (navigate, lookup_record, fetch_record) is not filtered, as with app filters."""

    async def test_internal_search_answers_only_from_the_document_that_states_it(
        self, conversations_client, ner_corpus,
    ) -> None:
        trace = _turn(
            conversations_client, ner_corpus, "What amounts of money do the documents mention?",
            mode="internal_search", entity_filters=REFUND,
        )
        try:
            cited, _virtual = _cited_ids(trace.citations)
            assert cited, trace.describe()
            assert cited <= {ner_corpus.record_ids["settlement"]}, trace.describe()
            assert "1250000" not in trace.answer.replace(",", ""), trace.describe()
        finally:
            delete_conversation(conversations_client, trace)

    async def test_every_agent_content_search_is_scoped_by_it(self, conversations_client, ner_corpus) -> None:
        trace = _turn(
            conversations_client, ner_corpus, "Search the documents for the amounts of money they mention.",
            mode="agent", entity_filters=REFUND,
        )
        try:
            searches = [call for call in trace.tool_calls if call.name.endswith(_CONTENT_SEARCHES)]
            assert searches, trace.describe()
            returned = " ".join(call.result for call in searches)
            assert ner_corpus.record_ids["contract"] not in returned, returned[:600]
            assert "1,250,000" not in returned, returned[:600]
            assert ner_corpus.record_ids["settlement"] in returned or "742" in returned, returned[:600]
        finally:
            delete_conversation(conversations_client, trace)

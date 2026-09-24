"""Did the turn actually retrieve anything, and does what it cited still exist?

Both cover failures that leave a *successful-looking* answer behind, which is
why the existing conversation tests cannot see them: the stream reaches
RUN_FINISHED, the answer is non-empty, the schema matches -- and the model
simply answered with no source material.

* Retrieval silently returning nothing. An explicit ``limit: null`` used to
  survive as ``None`` all the way to ``req.limit * 2`` in the vector store,
  where the ``TypeError`` was caught and logged as "Filtered search failed" and
  turned into an empty result set. Guarded now in `retrieval_service.py`; this
  is what keeps it guarded.
* Citations pointing at records that cannot be fetched, which is the failure
  mode a batched graph/storage lookup introduces if it drops or mismatches
  rows.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.agui_sse import (  # noqa: E402
    is_conversation_created,
    is_root_error,
    is_root_finished,
    iter_sse_envelopes,
)
from helper.clients.conversations_client import ConversationsClient  # noqa: E402
from helper.clients.kb_client import KBClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402

# Answerable only from the Asana PDF in `session_kb`, so a turn that retrieves
# nothing cannot bluff its way to a citation.
GROUNDED_QUERY = "every year asana undertakes which exercise?"

pytestmark = pytest.mark.xdist_group("retrieval-integrity")


def _stream_and_collect(
    conversations: ConversationsClient, timeout: int, **body: Any
) -> tuple[str, bool]:
    """Run one turn to completion. Returns (conversation_id, saw_run_finished)."""
    conversation_id = ""
    finished = False
    with conversations.stream_conversation(
        query=GROUNDED_QUERY, chatMode="internal_search", timeout=timeout, **body
    ) as resp:
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        for envelope in iter_sse_envelopes(resp):
            payload = json.loads(envelope["data"])
            event = envelope["event"]

            if is_conversation_created(payload):
                value = payload.get("value") or payload
                conversation_id = str(
                    value.get("conversationId") or value.get("_id") or conversation_id
                )
            if is_root_error(event, payload):
                raise AssertionError(f"stream emitted RUN_ERROR: {payload!r}")
            if is_root_finished(event, payload):
                finished = True
                break
    return conversation_id, finished


def _bot_citations(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    """Every citation attached to a bot message in the conversation."""
    convo = conversation.get("conversation") or conversation
    citations: list[dict[str, Any]] = []
    for message in convo.get("messages") or []:
        for citation in message.get("citations") or []:
            if isinstance(citation, dict):
                citations.append(citation)
    return citations


def _record_ids(citations: list[dict[str, Any]]) -> list[str]:
    """Record ids the citations point at, where the payload exposes one.

    Citations are returned either as bare references or already populated, so
    look in both shapes rather than assuming one.
    """
    ids: list[str] = []
    for citation in citations:
        block = citation.get("citationData") or citation.get("citation") or citation
        metadata = block.get("metadata") if isinstance(block, dict) else None
        record_id = None
        if isinstance(metadata, dict):
            record_id = metadata.get("recordId")
        if not record_id and isinstance(block, dict):
            record_id = block.get("recordId")
        if record_id and str(record_id) not in ids:
            ids.append(str(record_id))
    return ids


@pytest.mark.integration
class TestRetrievalIntegrity:

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        conversations_client: ConversationsClient,
        pipeshub_client: PipeshubClient,
        session_kb: dict,
    ) -> None:
        self.conversations = conversations_client
        self.client = pipeshub_client
        self.kb_client = KBClient(pipeshub_client)
        self.kb_id = session_kb["kb_id"]
        self.record_id = session_kb["record_id"]
        timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        override = os.getenv("PIPESHUB_TEST_STREAM_TIMEOUT", "").strip()
        self.stream_timeout = int(override) if override else max(timeout, 120)

    def test_grounded_turn_cites_its_sources(self) -> None:
        """A question answerable only from the KB comes back with citations."""
        conversation_id, finished = _stream_and_collect(
            self.conversations, self.stream_timeout, filters={"kb": [self.kb_id]}
        )
        assert finished, "stream ended without RUN_FINISHED"
        assert conversation_id, "stream never reported a conversation id"

        resp = self.conversations.get_conversation(
            conversation_id, timeout=self.stream_timeout
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        citations = _bot_citations(resp.json())
        assert citations, (
            "The answer carried no citations at all. The question is answerable "
            "only from the knowledge base, so a turn with no sources means "
            "retrieval returned nothing and the model answered unaided."
        )

    def test_cited_records_are_retrievable(self) -> None:
        """Everything the answer cites can still be fetched.

        Guards the batched graph and storage lookups: dropping or mismatching a
        row there produces citations that point at nothing, which no
        schema-shape assertion would catch.
        """
        conversation_id, finished = _stream_and_collect(
            self.conversations, self.stream_timeout, filters={"kb": [self.kb_id]}
        )
        assert finished, "stream ended without RUN_FINISHED"
        assert conversation_id, "stream never reported a conversation id"

        resp = self.conversations.get_conversation(
            conversation_id, timeout=self.stream_timeout
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        citations = _bot_citations(resp.json())
        assert citations, "no citations to check — see test_grounded_turn_cites_its_sources"

        record_ids = _record_ids(citations)
        if not record_ids:
            pytest.skip(
                "Citations in this deployment do not expose a recordId; nothing to resolve"
            )
        for record_id in record_ids:
            record = self.kb_client.get_record(record_id)
            body = record.get("record") or record
            assert body.get("id") or body.get("_key"), (
                f"Cited record {record_id} could not be fetched: {record!r}"
            )

    def test_explicit_null_limit_still_retrieves(self) -> None:
        """`limit: null` must not quietly disable retrieval.

        `ChatQuery.limit` defaults to 50, so *omitting* it is safe -- only an
        explicit null reaches the code path that broke. It failed silently: the
        turn still finished and still answered, just with nothing retrieved, so
        the only way to see it is to check that sources came back.
        """
        conversation_id, finished = _stream_and_collect(
            self.conversations,
            self.stream_timeout,
            filters={"kb": [self.kb_id]},
            limit=None,
        )
        assert finished, "stream with limit=null ended without RUN_FINISHED"
        assert conversation_id, "stream with limit=null never reported a conversation id"

        resp = self.conversations.get_conversation(
            conversation_id, timeout=self.stream_timeout
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert _bot_citations(resp.json()), (
            "A turn sent with limit=null produced no citations, while the same "
            "question cites sources normally — retrieval silently returned an "
            "empty result set instead of falling back to the default limit."
        )

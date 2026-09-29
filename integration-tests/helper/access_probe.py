"""Ask the product, as one person, whether they can reach one record.

Three user-facing paths, each answered the way that person would see it:

* search: does a word found only in that record bring the record back;
* open: does the record page load (``GET /knowledgeBase/record/:id``);
* chat: does an answer cite the record.

Each probe returns what it saw, so a test can tell "refused" from "the server
failed". A 5xx or an expired login is never read as "no access": that would let
a broken stack pass every "cannot see it" check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests

from helper.agui_sse import (
    is_root_error,
    is_root_finished,
    iter_sse_envelopes,
    run_error_message,
    run_finished_result,
)
from helper.second_user import NO_ACCESS_STATUSES, SecondUser
from retrieval.ranking import virtual_id_of

SEARCH_LIMIT = 10
CHAT_TIMEOUT_SEC = 240
# Chat answers are written by a model, which now and then answers without a
# citation even when retrieval found the record. One more try tells that apart
# from retrieval having found nothing.
CHAT_ATTEMPTS = 2


@dataclass(frozen=True)
class SearchOutcome:
    status: int
    virtual_ids: tuple[str, ...]

    def found(self, virtual_id: str) -> bool:
        return self.status == 200 and virtual_id in self.virtual_ids

    def refused_or_missing(self, virtual_id: str) -> bool:
        """The record is not reachable, and the answer proves it (not an error)."""
        if self.status in NO_ACCESS_STATUSES:
            return True
        return self.status == 200 and virtual_id not in self.virtual_ids

    def describe(self) -> str:
        return f"HTTP {self.status}, {len(self.virtual_ids)} hit(s)"


def search(user: SecondUser, word: str, kb_id: str | None = None) -> SearchOutcome:
    """Search as ``user``; scoped to one knowledge base, or everything they can reach."""
    filters = {"kb": [kb_id]} if kb_id else {}
    resp = user.search_filtered(word, filters, SEARCH_LIMIT)
    if resp.status_code != 200:
        return SearchOutcome(resp.status_code, ())
    body = resp.json()
    hits = (body.get("searchResponse") or body).get("searchResults") or []
    ids = tuple(vid for vid in (virtual_id_of(hit) for hit in hits) if vid)
    return SearchOutcome(200, ids)


def open_status(user: SecondUser, record_id: str) -> int:
    return user.get(f"/api/v1/knowledgeBase/record/{record_id}").status_code


def listed_kb_ids(user: SecondUser, name_prefix: str) -> set[str] | None:
    """Knowledge bases this person sees in their list, among those named ``name_prefix*``.

    ``None`` when the list itself could not be read.
    """
    resp = requests.get(
        f"{user.base_url}/api/v1/knowledgeBase",
        headers=user.headers,
        params={"page": "1", "limit": "50", "search": name_prefix},
        timeout=user.timeout,
    )
    if resp.status_code != 200:
        return None
    return {str(kb.get("id")) for kb in resp.json().get("knowledgeBases") or []}


@dataclass
class ChatAnswer:
    status: int
    finished: bool = False
    error: str | None = None
    cited_record_ids: set[str] = field(default_factory=set)
    cited_virtual_ids: set[str] = field(default_factory=set)

    def cites(self, record_id: str, virtual_id: str) -> bool:
        return record_id in self.cited_record_ids or virtual_id in self.cited_virtual_ids

    def describe(self) -> str:
        state = "finished" if self.finished else (f"error {self.error!r}" if self.error else "no end")
        return (
            f"HTTP {self.status}, {state}, cited records {sorted(self.cited_record_ids)}"
        )


def _citations(result: dict[str, Any]) -> list[dict[str, Any]]:
    conversation = result.get("conversation") or {}
    messages = conversation.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("citations"):
            return [c for c in message["citations"] if isinstance(c, dict)]
    return []


def _cited_ids(citations: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    record_ids: set[str] = set()
    virtual_ids: set[str] = set()
    for citation in citations:
        block = citation.get("citationData") or citation.get("citation") or citation
        metadata = block.get("metadata") if isinstance(block, dict) else None
        for source in (metadata, block):
            if not isinstance(source, dict):
                continue
            if source.get("recordId"):
                record_ids.add(str(source["recordId"]))
            if source.get("virtualRecordId"):
                virtual_ids.add(str(source["virtualRecordId"]))
    return record_ids, virtual_ids


def ask_once(user: SecondUser, question: str, kb_ids: list[str] | None = None) -> ChatAnswer:
    body: dict[str, Any] = {"query": question, "chatMode": "internal_search"}
    if kb_ids:
        body["filters"] = {"kb": kb_ids}
    with requests.post(
        f"{user.base_url}/api/v1/conversations/stream",
        headers={**user.headers, "Accept": "text/event-stream"},
        json=body,
        stream=True,
        timeout=CHAT_TIMEOUT_SEC,
    ) as resp:
        answer = ChatAnswer(status=resp.status_code)
        if resp.status_code != 200:
            return answer
        for envelope in iter_sse_envelopes(resp):
            try:
                payload = json.loads(envelope["data"])
            except json.JSONDecodeError:
                continue
            event = envelope["event"]
            if is_root_error(event, payload):
                answer.error = run_error_message(payload) or "RUN_ERROR"
                return answer
            if is_root_finished(event, payload):
                answer.finished = True
                records, virtuals = _cited_ids(_citations(run_finished_result(payload)))
                answer.cited_record_ids, answer.cited_virtual_ids = records, virtuals
                return answer
    return answer


def ask_until_cited(
    user: SecondUser, question: str, record_id: str, virtual_id: str, kb_ids: list[str] | None = None,
) -> ChatAnswer:
    """Ask up to ``CHAT_ATTEMPTS`` times; return the first answer citing the record, else the last."""
    answer = ChatAnswer(status=0)
    for _ in range(CHAT_ATTEMPTS):
        answer = ask_once(user, question, kb_ids)
        if answer.cites(record_id, virtual_id):
            return answer
    return answer


def chat_refused_or_uncited(answer: ChatAnswer, record_id: str, virtual_id: str) -> bool:
    """The answer proves the record stayed out of reach.

    A refusal before the stream starts, or a stream that ended (finished or
    with the product's own error) without citing it. A server failure proves
    nothing and does not count.
    """
    if answer.status in NO_ACCESS_STATUSES:
        return True
    if answer.status != 200:
        return False
    if not (answer.finished or answer.error):
        return False
    return not answer.cites(record_id, virtual_id)

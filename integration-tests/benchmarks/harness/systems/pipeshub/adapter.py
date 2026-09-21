"""PipesHub as a system under test.

Every question goes through the product's public streaming endpoint in
`internal_search` mode — never `web_search` or `agent` (anti-cheating) —
scoped to the benchmark KB, with the opt-in retrieval trace switched on.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import requests

from agui_sse import iter_sse_envelopes
from benchmarks.harness.config import ModelPrice
from benchmarks.harness.errors import StreamError, StreamProtocolError, StreamTimeoutError, TransientHTTPError
from benchmarks.harness.guard import ToolCallGuard
from benchmarks.harness.llm.client import ResolvedModel
from benchmarks.harness.models import AskItem, Prediction, RankedList, SystemFailure
from benchmarks.harness.retry import RETRYABLE_STATUS, retry_after_seconds
from benchmarks.harness.systems.base import AdapterCapabilities, CorpusIngestor, PreparedCorpus, RankedRetriever
from benchmarks.harness.systems.baselines.answering import usage_fields
from benchmarks.harness.systems.pipeshub.ingest import PipesHubIngestor
from benchmarks.harness.systems.pipeshub.session import UserSession
from benchmarks.harness.systems.pipeshub.stream import FinalAnswer, StreamCollector
from helper.clients.conversations_client import ConversationsClient

logger = logging.getLogger(__name__)

CHAT_MODE = "internal_search"
_MAX_EVENTS = 50_000
_CONNECT_TIMEOUT_S = 15
_PRE_STREAM_RETRY_DELAY_S = 5.0
_SEARCH_TIMEOUT_S = 180


def build_stream_body(item: AskItem, kb_id: str, model: ResolvedModel, current_time: datetime) -> dict[str, Any]:
    """The request body. Carries the question verbatim and nothing else about it."""
    return {
        "query": item.prompt,
        "chatMode": CHAT_MODE,
        "filters": {"kb": [kb_id]},
        "modelKey": model.model_key,
        "modelName": model.model_name,
        "timezone": "UTC",
        "currentTime": current_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "includeRetrievalContext": True,
        **({"reasoningEffort": model.reasoning_effort} if model.reasoning_effort else {}),
    }


def _search_record_ids(payload: dict[str, Any]) -> list[str]:
    results = (payload.get("searchResponse") or {}).get("searchResults") or []
    ids = ((r.get("metadata") or {}).get("recordId") for r in results if isinstance(r, dict))
    return list(dict.fromkeys(rid for rid in ids if rid))


class PipesHubAdapter:
    capabilities = AdapterCapabilities(ingests_corpus=True, retrieval_trace=True, citations=True, ranked_search=True)

    def __init__(
        self,
        system_id: str,
        session: UserSession,
        ingestor: PipesHubIngestor,
        model: ResolvedModel,
        guard: ToolCallGuard,
        *,
        current_time: datetime,
        stream_timeout_s: int,
        price: ModelPrice | None = None,
    ) -> None:
        self.system_id = system_id
        self._session = session
        self._ingestor = ingestor
        self._model = model
        self._guard = guard
        self._current_time = current_time
        self._stream_timeout_s = stream_timeout_s
        self._price = price

    def ingestor(self) -> CorpusIngestor | None:
        return self._ingestor

    def retriever(self) -> RankedRetriever | None:
        return self

    @staticmethod
    def _kb_id(prepared: PreparedCorpus) -> str:
        if prepared.ingest is None:
            raise StreamProtocolError("PipesHub corpus was not ingested")
        return prepared.ingest.kb_id

    def answer(self, item: AskItem, prepared: PreparedCorpus, repeat: int) -> Prediction:
        body = build_stream_body(item, self._kb_id(prepared), self._model, self._current_time)
        base = Prediction(system=self.system_id, question_id=item.question_id, repeat=repeat, started_at=datetime.now(UTC))
        started = time.monotonic()
        try:
            trace, final, failure = self._stream_with_retry(body)
        except (StreamError, TransientHTTPError, requests.RequestException) as exc:
            kind = "timeout" if isinstance(exc, StreamTimeoutError) else "transport"
            return base.model_copy(update={
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": SystemFailure(kind=kind, message=str(exc)[:1000]),
            })
        if final.llm_calls is None and failure is None:
            logger.warning("q%s: no run_usage frame — deploy a backend that reports usage", item.question_id)
        return base.model_copy(update={
            "answer": final.answer, "citations": final.citations, "trace": trace, "error": failure,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "policy_violations": self._guard.violations(call.name for call in trace.tool_calls),
            **(usage_fields(final.llm_calls, self._price) if final.llm_calls is not None else {}),
        })

    def _stream_with_retry(self, body: dict[str, Any]) -> tuple[Any, FinalAnswer, SystemFailure | None]:
        """Retry once, only when nothing was streamed yet: a retry after the
        first frame would create a second conversation for the same question."""
        collector = StreamCollector()
        try:
            return self._stream(body, collector)
        except (TransientHTTPError, requests.ConnectionError) as exc:
            if collector.frames_seen:
                raise
            delay = exc.retry_after if isinstance(exc, TransientHTTPError) and exc.retry_after else _PRE_STREAM_RETRY_DELAY_S
            logger.warning("stream failed before first frame (%s); retrying in %.0fs", exc, delay)
            time.sleep(delay)
            return self._stream(body, StreamCollector())

    def _stream(self, body: dict[str, Any], collector: StreamCollector) -> tuple[Any, FinalAnswer, SystemFailure | None]:
        deadline = time.monotonic() + self._stream_timeout_s
        conversations = ConversationsClient(self._session)
        with conversations.stream_conversation(json=body, timeout=(_CONNECT_TIMEOUT_S, self._stream_timeout_s)) as resp:
            if resp.status_code in RETRYABLE_STATUS:
                raise TransientHTTPError(resp.status_code, retry_after_seconds(resp), resp.url or "")
            if resp.status_code >= 400:
                trace, final, _ = collector.result()
                return trace, final, SystemFailure(kind="http", code=str(resp.status_code), message=resp.text[:1000])
            try:
                for envelope in iter_sse_envelopes(resp, max_events=_MAX_EVENTS):
                    if time.monotonic() > deadline:
                        raise StreamTimeoutError(f"stream exceeded {self._stream_timeout_s}s")
                    collector.feed(envelope)
            except AssertionError as exc:
                raise StreamProtocolError(str(exc)) from exc
        return collector.result()

    def ranked_search(self, item: AskItem, prepared: PreparedCorpus, k: int) -> RankedList:
        ingest = prepared.ingest
        url_by_record = {r.record_id: r.canonical_url for r in ingest.records} if ingest else {}
        try:
            resp = self._session.request(
                "POST", "/api/v1/search",
                json={"query": item.prompt, "filters": {"kb": [self._kb_id(prepared)]}, "limit": k},
                timeout=_SEARCH_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            # One slow search costs this question's ranking metrics, not the run.
            logger.warning("search q%s failed: %s", item.question_id, exc)
            return RankedList(system=self.system_id, question_id=item.question_id, ranked_refs=[])
        if resp.status_code >= 400:
            logger.warning("search q%s: HTTP %s", item.question_id, resp.status_code)
            return RankedList(system=self.system_id, question_id=item.question_id, ranked_refs=[])
        urls = [url_by_record[rid] for rid in _search_record_ids(resp.json()) if rid in url_by_record]
        return RankedList(system=self.system_id, question_id=item.question_id, ranked_refs=list(dict.fromkeys(urls)))

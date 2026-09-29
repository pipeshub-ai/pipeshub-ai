"""RAGFlow as a system under test: its own HTML parsing, chunking, weighted
hybrid retrieval and RAG prompt, answering through the shared model.

One non-streaming `/chat/completions` call per question. The chat is created
on first use from the run config's settings (model, top-n, retrieval
weights), bound to the corpus dataset. It keeps RAGFlow's default system
prompt unless the run config gives one, and appends one line giving the
corpus snapshot as today's date -- the same "today" PipesHub and the RAG
baselines get. Every call opens a fresh session that is not stored.

With `reasoning` set, each question runs RAGFlow's agentic research loop.
Its answer is the loop's final `rag` output, which ends with a status note
addressed to the loop itself; that note is removed. `reference` there is the
whole pool the loop gathered, and is empty whenever the answer cites
nothing, so an empty reference is not taken as a retrieval failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from benchmarks.harness.evidence import captured, empty, unavailable
from benchmarks.harness.models import AskItem, Evidence, EvidencePassage, Prediction, RetrievedChunk, SystemFailure
from benchmarks.harness.systems.base import AdapterCapabilities, PreparedCorpus, no_context_failure

if TYPE_CHECKING:
    from benchmarks.harness.systems.base import CorpusIngestor, RankedRetriever
    from benchmarks.harness.systems.ragflow.client import RagflowClient

logger = logging.getLogger(__name__)


def retrieved_chunks(data: dict[str, Any], url_of_document: dict[str, str]) -> list[RetrievedChunk]:
    """The chunks RAGFlow put in the prompt, in its order, mapped to corpus
    URLs. `reference.chunks` lists every chunk given to the model, cited or
    not; `doc_aggs` would list only the cited documents."""
    reference = data.get("reference") or {}
    chunks: list[RetrievedChunk] = []
    for chunk in reference.get("chunks") or [] if isinstance(reference, dict) else []:
        url = url_of_document.get(str(chunk.get("document_id", "")))
        if url is None:
            continue
        score = chunk.get("similarity")
        chunks.append(RetrievedChunk(
            url=url, virtual_record_id=str(chunk.get("document_id")),
            score=float(score) if isinstance(score, (int, float)) else 0.0,
        ))
    return chunks


EVIDENCE_SOURCE = "ragflow_reference_chunks"


def evidence_of(data: dict[str, Any], *, reasoning: bool) -> Evidence:
    """`reference.chunks[].content`: the chunks RAGFlow gave the model. The
    research loop reports its pool only when the answer cites it, so an
    empty reference there is unknown context, not no context."""
    reference = data.get("reference") or {}
    chunks = reference.get("chunks") or [] if isinstance(reference, dict) else []
    passages = [
        EvidencePassage(header=f"{chunk.get('document_name')}\n" if chunk.get("document_name") else "", text=chunk["content"])
        for chunk in chunks
        if isinstance(chunk, dict) and isinstance(chunk.get("content"), str) and chunk["content"]
    ]
    if passages:
        return captured(passages, EVIDENCE_SOURCE)
    if chunks:
        return unavailable(EVIDENCE_SOURCE, "RAGFlow returned reference chunks without text")
    if reasoning:
        return unavailable(EVIDENCE_SOURCE, "RAGFlow's research loop reported no reference pool")
    return empty(EVIDENCE_SOURCE)


_RESEARCH_STATUS = "\n\n[Research status]"


def final_answer(answer: str) -> str:
    """The answer without the reasoning loop's note to itself."""
    return answer.split(_RESEARCH_STATUS, 1)[0].rstrip()


def chat_name(
    dataset_id: str, chat_config: dict[str, Any], system_prompt: str | None,
    prompt_options: dict[str, Any] | None = None,
) -> str:
    """Keyed by the chat's settings: a chat is reused as found, so different
    settings must never land on an existing chat."""
    settings = json.dumps(
        {"chat": chat_config, "system_prompt": system_prompt, "prompt_options": prompt_options or {}}, sort_keys=True,
    )
    return f"frames-chat-{dataset_id}-{hashlib.sha256(settings.encode()).hexdigest()[:8]}"


def date_line(current_time: datetime) -> str:
    return f"\n\nToday's date is {current_time.astimezone(UTC).date().isoformat()}."


class RagflowAdapter:
    capabilities = AdapterCapabilities(ingests_corpus=True, reads_index="ragflow")

    def __init__(
        self,
        system_id: str,
        client: RagflowClient,
        ingestor: CorpusIngestor,
        *,
        chat_config: dict[str, Any],
        current_time: datetime,
        system_prompt: str | None = None,
        prompt_options: dict[str, Any] | None = None,
        reasoning: str | None = None,
    ) -> None:
        self.system_id = system_id
        self._client = client
        self._ingestor = ingestor
        self._chat_config = chat_config
        self._system_prompt = system_prompt
        self._prompt_options = dict(prompt_options or {})
        self._reasoning = reasoning
        self._current_time = current_time
        self._chat_id: str | None = None
        self._chat_lock = threading.Lock()
        self._url_of_document: dict[str, str] | None = None

    def _chat(self, prepared: PreparedCorpus) -> str:
        """The chat bound to the corpus dataset, created on first use."""
        with self._chat_lock:
            if self._chat_id is not None:
                return self._chat_id
            if prepared.ingest is None:
                raise ValueError(f"{self.system_id}: corpus was not ingested")
            name = chat_name(prepared.ingest.kb_id, self._chat_config, self._system_prompt, self._prompt_options)
            chat_id = self._client.chat_id(name)
            if chat_id is None:
                chat_id = self._client.create_chat(
                    {**self._chat_config, "name": name, "dataset_ids": [prepared.ingest.kb_id]},
                )
                prompt_config = dict(self._client.get_chat(chat_id).get("prompt_config") or {})
                system = self._system_prompt if self._system_prompt is not None else prompt_config.get("system", "")
                prompt_config["system"] = str(system) + date_line(self._current_time)
                prompt_config.update(self._prompt_options)
                self._client.update_chat(chat_id, {"prompt_config": prompt_config})
            self._chat_id = chat_id
            return chat_id

    def _document_urls(self, prepared: PreparedCorpus) -> dict[str, str]:
        if self._url_of_document is None:
            records = prepared.ingest.records if prepared.ingest else []
            self._url_of_document = {r.record_id: r.canonical_url for r in records}
        return self._url_of_document

    def answer(self, item: AskItem, prepared: PreparedCorpus, repeat: int) -> Prediction:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        base = Prediction(system=self.system_id, question_id=item.question_id, repeat=repeat, started_at=started_at)
        try:
            data = self._client.complete(self._chat(prepared), item.prompt, self._reasoning)
        except Exception as exc:  # noqa: BLE001 — recorded on the prediction and scored FALSE
            logger.warning("%s q%s failed: %s", self.system_id, item.question_id, exc)
            return base.model_copy(update={
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": SystemFailure(kind="http", message=str(exc)[:1000]),
            })
        chunks = retrieved_chunks(data, self._document_urls(prepared))
        # RAGFlow reports no token usage, so tokens and cost stay unknown
        # rather than estimated.
        return base.model_copy(update={
            "answer": final_answer(str(data.get("answer") or "")),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "retrieved": chunks,
            "context_urls": list(dict.fromkeys(c.url for c in chunks)),
            "error": None if self._reasoning else no_context_failure(chunks),
            "evidence": evidence_of(data, reasoning=bool(self._reasoning)),
        })

    def ingestor(self) -> CorpusIngestor | None:
        return self._ingestor

    def retriever(self) -> RankedRetriever | None:
        return None

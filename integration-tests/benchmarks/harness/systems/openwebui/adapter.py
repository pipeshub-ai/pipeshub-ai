"""Open WebUI as a system under test: its own parsing, chunking, hybrid
retrieval and RAG prompt, answering through the shared model.

One non-streaming `/api/chat/completions` call per question with the corpus
knowledge base attached. No `chat_id` or `session_id` is sent, so every call
is a fresh chat and Open WebUI runs none of its background tasks (title,
tags, follow-ups) or tool loop.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from benchmarks.harness.models import AskItem, CallUsage, Prediction, RetrievedChunk, SystemFailure
from benchmarks.harness.systems.base import AdapterCapabilities, PreparedCorpus
from benchmarks.harness.systems.baselines.answering import usage_fields

if TYPE_CHECKING:
    from benchmarks.harness.llm.client import ResolvedModel
    from benchmarks.harness.config import ModelPrice
    from benchmarks.harness.systems.base import CorpusIngestor, RankedRetriever
    from benchmarks.harness.systems.openwebui.client import OpenWebUIClient

logger = logging.getLogger(__name__)


def build_chat_body(
    item: AskItem, knowledge_id: str, model: str, reasoning_effort: str | None, current_time: datetime,
) -> dict[str, Any]:
    """The request for one question.

    The date line gives Open WebUI the same "today" PipesHub and the RAG
    baselines get: the corpus snapshot, not the wall clock.
    """
    body: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": f"Today's date is {current_time.astimezone(UTC).date().isoformat()}."},
            {"role": "user", "content": item.prompt},
        ],
        "files": [{"type": "collection", "id": knowledge_id}],
    }
    if reasoning_effort:
        # Open WebUI passes unknown body keys through to the Responses API;
        # its own `reasoning_effort` model param is not translated there.
        body["reasoning"] = {"effort": reasoning_effort}
    return body


def answer_text(body: dict[str, Any]) -> str:
    choices = body.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
    output = body.get("output_text")
    return output if isinstance(output, str) else ""


def call_usage_of(body: dict[str, Any]) -> list[CallUsage]:
    usage = body.get("usage") or {}
    if not usage:
        return []
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    return [CallUsage(
        input_tokens=int(input_tokens), output_tokens=int(output_tokens),
        cached_tokens=int(details.get("cached_tokens", 0) or 0), purpose="answer",
    )]


def retrieved_chunks(body: dict[str, Any], url_of_file: dict[str, str]) -> list[RetrievedChunk]:
    """Chunks Open WebUI put in the prompt, in its order, mapped to corpus URLs.

    Each `sources` entry carries parallel `document`, `metadata` and
    `distances` lists; `metadata[i].file_id` names the uploaded file.
    """
    chunks: list[RetrievedChunk] = []
    for source in body.get("sources") or []:
        metadata = source.get("metadata") or []
        distances = source.get("distances") or []
        for i, meta in enumerate(metadata):
            url = url_of_file.get(str((meta or {}).get("file_id", "")))
            if url is None:
                continue
            score = distances[i] if i < len(distances) and isinstance(distances[i], (int, float)) else 0.0
            chunks.append(RetrievedChunk(url=url, virtual_record_id=str(meta.get("file_id")), score=float(score)))
    return chunks


class OpenWebUIAdapter:
    capabilities = AdapterCapabilities(ingests_corpus=True, reads_index="openwebui")

    def __init__(
        self,
        system_id: str,
        client: OpenWebUIClient,
        ingestor: CorpusIngestor,
        model: ResolvedModel,
        *,
        reasoning_effort: str | None,
        current_time: datetime,
        price: ModelPrice | None,
    ) -> None:
        self.system_id = system_id
        self._client = client
        self._ingestor = ingestor
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._current_time = current_time
        self._price = price
        self._url_of_file: dict[str, str] | None = None

    def _file_urls(self, prepared: PreparedCorpus) -> dict[str, str]:
        if self._url_of_file is None:
            records = prepared.ingest.records if prepared.ingest else []
            self._url_of_file = {r.record_id: r.canonical_url for r in records}
        return self._url_of_file

    def answer(self, item: AskItem, prepared: PreparedCorpus, repeat: int) -> Prediction:
        if prepared.ingest is None:
            raise ValueError(f"{self.system_id}: corpus was not ingested")
        started_at = datetime.now(UTC)
        started = time.monotonic()
        body = build_chat_body(
            item, prepared.ingest.kb_id, self._model.deployment or self._model.model_name,
            self._reasoning_effort, self._current_time,
        )
        base = Prediction(system=self.system_id, question_id=item.question_id, repeat=repeat, started_at=started_at)
        try:
            response = self._client.chat(body)
        except Exception as exc:  # noqa: BLE001 — recorded on the prediction and scored FALSE
            logger.warning("%s q%s failed: %s", self.system_id, item.question_id, exc)
            return base.model_copy(update={
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": SystemFailure(kind="http", message=str(exc)[:1000]),
            })
        chunks = retrieved_chunks(response, self._file_urls(prepared))
        return base.model_copy(update={
            "answer": answer_text(response),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "retrieved": chunks,
            "context_urls": list(dict.fromkeys(c.url for c in chunks)),
            **usage_fields(call_usage_of(response), self._price),
        })

    def ingestor(self) -> CorpusIngestor | None:
        return self._ingestor

    def retriever(self) -> RankedRetriever | None:
        return None

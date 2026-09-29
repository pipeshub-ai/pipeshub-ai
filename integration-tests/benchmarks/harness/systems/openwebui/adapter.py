"""Open WebUI as a system under test: its own parsing, chunking, hybrid
retrieval and RAG prompt, answering through the shared model.

One non-streaming `/api/chat/completions` call per question with the corpus
knowledge base attached. No `chat_id` or `session_id` is sent, so every call
is a fresh chat and Open WebUI runs none of its background tasks (title,
tags, follow-ups) or tool loop. The instance-wide settings a run chooses
(query generation, RAG template) are checked, and set when they drifted,
around every question: Open WebUI reloads them from its environment on
restart, and keeps whatever the previous run set until then.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from benchmarks.harness.evidence import captured, empty, unavailable
from benchmarks.harness.models import AskItem, CallUsage, Evidence, EvidencePassage, Prediction, RetrievedChunk, SystemFailure
from benchmarks.harness.systems.base import AdapterCapabilities, PreparedCorpus, no_context_failure
from benchmarks.harness.systems.baselines.answering import usage_fields
from benchmarks.harness.systems.openwebui.client import InstanceSettings

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


def build_ui_chat_body(
    item: AskItem, knowledge_id: str, model: str, reasoning_effort: str | None, current_time: datetime,
) -> tuple[dict[str, Any], str]:
    """The request the web UI sends for a new chat with the knowledge base
    attached, and the id of the assistant message it will produce.

    A `session_id` is what makes Open WebUI run the chat as its UI does:
    native tool calling with its builtin knowledge tools, alongside the
    up-front retrieval. No socket is needed; the result is saved to the chat.
    The date line goes in as the chat's system prompt.
    """
    user_id, assistant_id = str(uuid.uuid4()), str(uuid.uuid4())
    files = [{"type": "collection", "id": knowledge_id}]
    body: dict[str, Any] = {
        "model": model,
        "stream": True,
        "params": {"system": f"Today's date is {current_time.astimezone(UTC).date().isoformat()}."},
        "features": {},
        "files": files,
        "session_id": f"frames-{uuid.uuid4()}",
        "id": assistant_id,
        "parent_id": None,
        "user_message": {
            "id": user_id, "parentId": None, "childrenIds": [assistant_id], "role": "user",
            "content": item.prompt, "files": files, "timestamp": int(time.time()), "models": [model],
        },
        "background_tasks": {},
    }
    if reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort}
    return body, assistant_id


def tool_calls(message: dict[str, Any]) -> list[str]:
    """The tools the model called, as `name(arguments)`, in order."""
    return [
        f"{item.get('name')}({item.get('arguments') if isinstance(item.get('arguments'), str) else json.dumps(item.get('arguments'))})"
        for item in message.get("output") or [] if isinstance(item, dict) and item.get("type") == "function_call"
    ]


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


EVIDENCE_SOURCE = "openwebui_sources"


def evidence_of(body: dict[str, Any]) -> Evidence:
    """The chunk texts Open WebUI put in the prompt (`sources[].document`,
    parallel to `metadata`). Every chunk counts, mapped to the corpus or not:
    the model read it either way."""
    passages: list[EvidencePassage] = []
    sources = body.get("sources") or []
    for source in sources:
        if not isinstance(source, dict):
            continue
        documents = source.get("document") or []
        metadata = source.get("metadata") or []
        for i, text in enumerate(documents):
            if not isinstance(text, str) or not text:
                continue
            meta = metadata[i] if i < len(metadata) and isinstance(metadata[i], dict) else {}
            name = meta.get("name") or meta.get("source") or (source.get("source") or {}).get("name") or ""
            passages.append(EvidencePassage(header=f"{name}\n" if name else "", text=text))
    if passages:
        return captured(passages, EVIDENCE_SOURCE)
    if sources:
        return unavailable(EVIDENCE_SOURCE, "Open WebUI returned sources without chunk text")
    return empty(EVIDENCE_SOURCE)


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
        settings: InstanceSettings | None = None,
        ui_chat: bool = False,
        poll_interval_s: float = 2.0,
        answer_timeout_s: float = 1_800.0,
    ) -> None:
        self.system_id = system_id
        self._client = client
        self._ingestor = ingestor
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._current_time = current_time
        self._price = price
        self._url_of_file: dict[str, str] | None = None
        self._settings = settings or InstanceSettings()
        self._ui_chat = ui_chat
        self._poll_interval_s = poll_interval_s
        self._answer_timeout_s = answer_timeout_s

    def _ensure_settings(self) -> None:
        if self._client.instance_settings() != self._settings:
            held = self._client.apply_settings(self._settings)
            if held != self._settings:
                raise RuntimeError(f"Open WebUI kept {held}, wanted {self._settings}")

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
        base = Prediction(system=self.system_id, question_id=item.question_id, repeat=repeat, started_at=started_at)
        try:
            self._ensure_settings()
            response = self._ui_answer(item, prepared) if self._ui_chat else self._client.chat(build_chat_body(
                item, prepared.ingest.kb_id, self._model.deployment or self._model.model_name,
                self._reasoning_effort, self._current_time,
            ))
            settings_held = self._client.instance_settings() == self._settings
        except Exception as exc:  # noqa: BLE001 — recorded on the prediction and scored FALSE
            logger.warning("%s q%s failed: %s", self.system_id, item.question_id, exc)
            return base.model_copy(update={
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": SystemFailure(kind="http", message=str(exc)[:1000]),
            })
        chunks = retrieved_chunks(response, self._file_urls(prepared))
        error = no_context_failure(chunks) or (None if settings_held else SystemFailure(
            kind="run_error", code="setting_lost",
            message="instance settings changed during the answer (Open WebUI restarted)",
        ))
        return base.model_copy(update={
            "answer": answer_text(response),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "retrieved": chunks,
            "context_urls": list(dict.fromkeys(c.url for c in chunks)),
            "queries": tool_calls(response),
            "error": error,
            "evidence": evidence_of(response),
            **usage_fields(call_usage_of(response), self._price),
        })

    def _ui_answer(self, item: AskItem, prepared: PreparedCorpus) -> dict[str, Any]:
        """The saved assistant message once it is done, shaped like a
        completion response: `content`, `sources`, `usage`, `output`."""
        assert prepared.ingest is not None
        body, message_id = build_ui_chat_body(
            item, prepared.ingest.kb_id, self._model.deployment or self._model.model_name,
            self._reasoning_effort, self._current_time,
        )
        chat_id = self._client.start_ui_chat(body)
        deadline = time.monotonic() + self._answer_timeout_s
        try:
            while True:
                chat = self._client.saved_chat(chat_id)
                message = (((chat.get("chat") or {}).get("history") or {}).get("messages") or {}).get(message_id) or {}
                if message.get("error"):
                    raise RuntimeError(f"Open WebUI: {str(message['error'])[:500]}")
                if message.get("done"):
                    return {
                        "choices": [{"message": {"content": message.get("content", "")}}],
                        "sources": message.get("sources") or [],
                        "usage": message.get("usage") or {},
                        "output": message.get("output") or [],
                    }
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Open WebUI chat {chat_id} not done after {self._answer_timeout_s}s")
                time.sleep(self._poll_interval_s)
        finally:
            try:
                self._client.delete_chat(chat_id)
            except Exception:  # noqa: BLE001 — a leftover chat only costs disk
                logger.warning("could not delete Open WebUI chat %s", chat_id)

    def ingestor(self) -> CorpusIngestor | None:
        return self._ingestor

    def retriever(self) -> RankedRetriever | None:
        return None

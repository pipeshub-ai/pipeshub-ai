"""Shared fakes for the FRAMES harness unit tests (not collected by pytest)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from benchmarks.datasets.frames.mediawiki import ParsedPage, RevisionRef
from benchmarks.harness.llm.client import LLMRequest, LLMResponse, ResolvedModel

REVISION_TIME = datetime(2024, 9, 1, tzinfo=UTC)


def make_model(name: str = "judge-model", provider: str = "anthropic", *, reasoning: bool = False) -> ResolvedModel:
    return ResolvedModel(model_key=f"key-{name}", provider=provider, model_name=name, is_reasoning=reasoning)


class FakeLLM:
    """Records every request; `responder` maps a request to reply text."""

    def __init__(self, responder: Callable[[LLMRequest], str]) -> None:
        self._responder = responder
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(text=self._responder(request), prompt_tokens=10, completion_tokens=5, cost_usd=0.001)


def html_page(body: str) -> str:
    return f'<div class="mw-parser-output">{body}</div>'


class FakeArticleSource:
    """In-memory MediaWiki: `pages` maps title -> body HTML; `redirects` maps title -> title."""

    def __init__(self, pages: dict[str, str], redirects: dict[str, str] | None = None) -> None:
        self.pages = pages
        self.redirects = redirects or {}
        self.parse_calls: list[int] = []
        self._revids = {title: 1000 + i for i, title in enumerate(sorted(pages))}

    def revision_at(self, title: str, snapshot: datetime) -> RevisionRef | None:
        title = self.redirects.get(title, title)
        if title not in self.pages:
            return None
        return RevisionRef(title=title, page_id=self._revids[title], revid=self._revids[title], timestamp=REVISION_TIME)

    def parse_revision(self, revid: int) -> ParsedPage:
        self.parse_calls.append(revid)
        title = next(t for t, r in self._revids.items() if r == revid)
        links = tuple(re.findall(r'href="/wiki/([^"#]+)"', self.pages[title]))
        return ParsedPage(revid=revid, title=title, html=html_page(self.pages[title]), links=links)

    def search_title(self, query: str) -> str | None:
        return query if query in self.pages else None

    def resolve_short_link(self, url: str) -> str:
        return "https://en.wikipedia.org/wiki/Freedonia"


def write_frames_tsv(path: Path, rows: list[dict[str, Any]]) -> Path:
    """A TSV shaped like google/frames-benchmark's test.tsv (unnamed index column)."""
    records = []
    for i, row in enumerate(rows):
        links = row["links"]
        record = {"": i, "Prompt": row["prompt"], "Answer": row["answer"]}
        for n in range(1, 11):
            record[f"wikipedia_link_{n}"] = links[n - 1] if n <= len(links) else ""
        record["wikipedia_link_11+"] = ""
        record["reasoning_types"] = row.get("types", "Multiple constraints")
        record["wiki_links"] = str(links)
        records.append(record)
    pd.DataFrame(records).to_csv(path, sep="\t", index=False)
    return path


def sse(frames: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {f['type']}\ndata: {json.dumps(f)}\n\n" for f in frames).encode()


class FakeStreamResponse:
    def __init__(self, body: bytes = b"", status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/event-stream"}
        self.url = "http://pipeshub.test/api/v1/conversations/stream"
        self._body = body
        self.text = body.decode(errors="replace")

    def iter_content(self, chunk_size: int = 512) -> Iterator[bytes]:
        for start in range(0, len(self._body), 37):  # odd chunking exercises frame reassembly
            yield self._body[start:start + 37]

    def json(self) -> Any:  # noqa: ANN401
        return json.loads(self.text)

    def __enter__(self) -> FakeStreamResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeSession:
    """Stands in for `UserSession`: `handler(method, path, kwargs)` returns a response."""

    def __init__(self, handler: Callable[[str, str, dict[str, Any]], Any]) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.auth_headers = {"Authorization": "Bearer test"}

    def request(self, method: str, path: str, *, auth: bool = True, **kwargs: Any) -> Any:  # noqa: ANN401
        self.calls.append((method, path, kwargs))
        return self._handler(method, path, kwargs)


def agui_transcript(*, answer: str = "Jane Ballou [1]", extra_tools: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """A realistic `internal_search` stream: prefetch frame, one search wave,
    a sub-agent fetch, the final snapshot and Node's RUN_FINISHED."""
    frames: list[dict[str, Any]] = [
        {"type": "CUSTOM", "name": "conversation_created", "value": {"conversationId": "conv-1"}},
        {"type": "RUN_STARTED", "runId": "run-1"},
        {"type": "CUSTOM", "name": "retrieval_context", "runId": "run-1", "value": {
            "schemaVersion": 1, "seq": 1, "source": "prefetch", "status": "ok",
            "records": [{"virtualRecordId": "vr-1", "recordId": "rec-1", "blockIndices": [2], "summaryHit": False, "fetched": []}],
            "knownRecordIds": [], "cumulativeBlocks": 1, "cumulativeRecords": 1,
        }},
        {"type": "TOOL_CALL_START", "toolCallId": "t1", "toolCallName": "knowledgegraph__search", "runId": "run-1"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "t1", "delta": '{"query": "first lady mother"}'},
        {"type": "TOOL_CALL_END", "toolCallId": "t1"},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "t1", "content": "Top 2 blocks", "status": "success", "resultSummary": "2 blocks"},
        {"type": "CUSTOM", "name": "retrieval_context", "runId": "run-1", "value": {
            "schemaVersion": 1, "seq": 2, "source": "tool", "status": "ok", "toolName": "knowledgegraph__search", "toolCallId": "t1",
            "records": [{"virtualRecordId": "vr-2", "recordId": "rec-2", "blockIndices": [0, 5], "summaryHit": False, "fetched": []}],
            "knownRecordIds": ["rec-9"], "cumulativeBlocks": 3, "cumulativeRecords": 2,
        }},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Thinking"},
        {"type": "TOOL_CALL_START", "toolCallId": "t2", "toolCallName": "knowledgegraph__fetch_record", "parentRunId": "run-1", "runId": "run-2"},
    ]
    for i, name in enumerate(extra_tools):
        frames.append({"type": "TOOL_CALL_START", "toolCallId": f"x{i}", "toolCallName": name, "runId": "run-1"})
    frames += [
        {"type": "STATE_SNAPSHOT", "runId": "run-1", "snapshot": {
            "final": True, "answer": answer, "confidence": "High",
            "citations": [{"content": "Harriet Lane's mother was Jane Buchanan", "chunkIndex": 1, "citationType": "vectordb",
                           "metadata": {"recordId": "rec-1", "virtualRecordId": "vr-1", "recordName": "Harriet_Lane--aaaa", "blockNum": [3]}}],
        }},
        {"type": "RUN_FINISHED", "result": {"conversation": {"messages": [{"messageType": "bot_response", "content": answer}]}}},
    ]
    return frames

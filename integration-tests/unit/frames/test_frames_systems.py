"""Baselines, the PipesHub session/ingest/index/stream/adapter."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import requests
from frames_testkit import (
    FakeArticleSource,
    FakeLLM,
    FakeSession,
    FakeStreamResponse,
    agui_transcript,
    make_model,
    sse,
)

from benchmarks.frames.config import FRAMES_SNAPSHOT
from benchmarks.frames.corpus.builder import CorpusBuilder
from benchmarks.frames.corpus.view import CorpusView
from benchmarks.frames.dataset.urls import normalize_wiki_url
from benchmarks.frames.errors import IndexTimeoutError
from benchmarks.frames.guard import ToolCallGuard
from benchmarks.frames.models import AskItem, IngestedRecord, IngestManifest
from benchmarks.frames.systems.base import PreparedCorpus
from benchmarks.frames.systems.baselines.answering import ANSWER_PROMPT_VERSION, ContextDocument, fit_documents
from benchmarks.frames.systems.baselines.bm25 import Bm25Answerer
from benchmarks.frames.systems.baselines.closed_book import ClosedBookAnswerer
from benchmarks.frames.systems.baselines.oracle import OracleAnswerer
from benchmarks.frames.systems.pipeshub.adapter import PipesHubAdapter, build_stream_body
from benchmarks.frames.systems.pipeshub.indexing import IndexWaiter
from benchmarks.frames.systems.pipeshub.ingest import PipesHubIngestor
from benchmarks.frames.systems.pipeshub.kb_api import RecordStatus, UploadResult
from benchmarks.frames.systems.pipeshub.session import PIPESHUB_CLIENT_ERRORS, UserSession
from benchmarks.frames.systems.pipeshub.stream import StreamCollector

PAGES = {
    "Harriet Lane": "<p>Harriet Lane was the niece of James Buchanan. Her mother was Jane Buchanan.</p>",
    "James A. Garfield": "<p>Garfield's mother was Eliza Ballou.</p>",
    "Duck Soup": "<p>A comedy film released in 1933 starring the Marx Brothers.</p>",
}


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusView:
    refs = [f"https://en.wikipedia.org/wiki/{t.replace(' ', '_')}" for t in PAGES]
    manifest = CorpusBuilder(
        lambda _h: FakeArticleSource(PAGES), tmp_path, snapshot=FRAMES_SNAPSHOT, workers=1, harness_version="t",
    ).build(refs, tier="G", distractor_count=0, seed=1, max_failed_gold_ratio=0.0)
    return CorpusView(tmp_path, manifest)


ITEM = AskItem(
    question_id="7", prompt="Whose mother was Eliza Ballou?",
    gold_refs=["https://en.wikipedia.org/wiki/James_A._Garfield"],
)
PREPARED = PreparedCorpus(system="s", corpus_version="v")


class TestBaselines:
    def test_fit_documents_truncates_the_last_document(self) -> None:
        docs = [ContextDocument("a", "A", "x" * 40), ContextDocument("b", "B", "y" * 40)]
        kept, truncated = fit_documents(docs, max_tokens=15)  # 60 chars
        assert [len(d.text) for d in kept] == [40, 20] and truncated

    def test_closed_book_sends_only_the_question(self) -> None:
        llm = FakeLLM(lambda _r: "Garfield")
        prediction = ClosedBookAnswerer("closed_book", llm, make_model()).answer(ITEM, PREPARED, 0)
        user = llm.requests[0].messages[-1].content
        assert user == f"Question: {ITEM.prompt}"
        assert prediction.answer == "Garfield" and prediction.context_urls == []
        assert llm.requests[0].prompt_version == ANSWER_PROMPT_VERSION

    def test_oracle_puts_the_gold_articles_in_context(self, corpus: CorpusView) -> None:
        llm = FakeLLM(lambda _r: "Garfield")
        prediction = OracleAnswerer("oracle", llm, make_model(), corpus).answer(ITEM, PREPARED, 0)
        assert prediction.context_urls == ["https://en.wikipedia.org/wiki/James_A._Garfield"]
        assert "Eliza Ballou" in llm.requests[0].messages[-1].content

    def test_bm25_ranks_the_matching_article_first(self, corpus: CorpusView) -> None:
        bm25 = Bm25Answerer("bm25", FakeLLM(lambda _r: "x"), make_model(), corpus, n_docs=1)
        ranking = bm25.ranked_search(ITEM, PREPARED, k=3)
        assert ranking.ranked_refs[0] == "https://en.wikipedia.org/wiki/James_A._Garfield"
        assert bm25.answer(ITEM, PREPARED, 0).context_urls == ranking.ranked_refs[:1]

    def test_llm_failure_is_recorded_not_raised(self) -> None:
        def _boom(_r: Any) -> str:  # noqa: ANN401
            raise RuntimeError("provider down")

        prediction = ClosedBookAnswerer("closed_book", FakeLLM(_boom), make_model()).answer(ITEM, PREPARED, 0)
        assert prediction.error is not None and prediction.error.kind == "llm"


def _jwt(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp, "orgId": "o", "userId": "u"}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


class _Resp:
    def __init__(self, status: int, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> None:
        self.status_code, self._body, self.headers, self.text = status, body or {}, headers or {}, json.dumps(body or {})

    def json(self) -> Any:  # noqa: ANN401
        return self._body


class TestUserSession:
    def test_logs_in_as_a_user_not_client_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        token = _jwt(4_102_444_800)

        def _fake(method: str, url: str, **kwargs: Any) -> _Resp:  # noqa: ANN401
            calls.append(url.rsplit("/", 1)[-1])
            if url.endswith("/initAuth"):
                return _Resp(200, headers={"x-session-token": "sess"})
            assert kwargs["headers"]["x-session-token"] == "sess"
            return _Resp(200, {"accessToken": token})

        monkeypatch.setattr(requests, "request", _fake)
        monkeypatch.setattr(requests, "post", _fake)
        session = UserSession("http://pipeshub.test", email="u@x.io", password="pw")
        assert session.auth_headers["Authorization"] == f"Bearer {token}"
        assert calls == ["initAuth", "authenticate"]
        assert "token" not in calls  # never the OAuth client_credentials endpoint


class FakeKbApi:
    def __init__(self, existing: dict[str, str] | None = None, fail_batches: bool = False) -> None:
        self.existing = dict(existing or {})
        self.uploads: list[list[str]] = []
        self.fail_batches = fail_batches
        self.statuses: list[dict[str, str]] = []
        self.reindexed: list[str] = []

    def create_kb(self, name: str) -> str:
        return "kb-1"

    def kb_exists(self, kb_id: str) -> bool:
        return True

    def list_records(self, kb_id: str) -> list[RecordStatus]:
        return [RecordStatus(record_id=rid, record_name=name, status="COMPLETED") for name, rid in self.existing.items()]

    def upload(self, kb_id: str, files: list[tuple[str, bytes, str]]) -> UploadResult:
        names = [f[0] for f in files]
        self.uploads.append(names)
        if self.fail_batches and len(names) > 1:
            raise PIPESHUB_CLIENT_ERRORS[0]("KB upload produced no successful records")
        return UploadResult(record_ids={n: f"rec-{n}" for n in names}, failed=[])

    def reindex(self, record_id: str) -> None:
        self.reindexed.append(record_id)


def _ingestor(api: FakeKbApi, corpus: CorpusView, tmp_path: Path) -> PipesHubIngestor:
    waiter = IndexWaiter(lambda _kb: {}, api.reindex, poll_interval_s=0, timeout_s=10)
    return PipesHubIngestor(
        api, system_id="pipeshub", base_url="http://pipeshub.test", corpus_dir=corpus.corpus_dir,
        cache_dir=tmp_path / "cache", batch_size=2, waiter=waiter,
    )


class TestIngest:
    def test_uploads_only_missing_records(self, corpus: CorpusView, tmp_path: Path) -> None:
        first_doc = corpus.manifest.documents[0]
        api = FakeKbApi(existing={first_doc.filename.removesuffix(".html"): "rec-existing"})
        prepared = _ingestor(api, corpus, tmp_path).prepare(corpus.manifest)
        uploaded = [name for batch in api.uploads for name in batch]
        assert first_doc.filename not in uploaded and len(uploaded) == 2
        assert prepared.ingest is not None and len(prepared.ingest.records) == 3
        assert {r.record_id for r in prepared.ingest.records} >= {"rec-existing"}

    def test_dropped_connection_uploads_only_what_did_not_land(self, corpus: CorpusView, tmp_path: Path) -> None:
        api = FakeKbApi()
        real_upload = api.upload
        dropped = []

        def flaky_upload(kb_id: str, files: list[tuple[str, bytes, str]]) -> UploadResult:
            if not dropped:
                dropped.append(True)
                first = files[0][0]
                api.existing[first.removesuffix(".html")] = f"rec-{first}"  # landed before the drop
                raise requests.exceptions.ChunkedEncodingError("Response ended prematurely")
            return real_upload(kb_id, files)

        api.upload = flaky_upload  # type: ignore[method-assign]
        ingestor = _ingestor(api, corpus, tmp_path)
        ingestor._sleep = lambda _s: None
        prepared = ingestor.prepare(corpus.manifest)
        assert len(prepared.ingest.records) == 3
        assert [len(b) for b in api.uploads] == [1, 1]

    def test_interrupted_ingest_resumes_into_the_same_kb(self, corpus: CorpusView, tmp_path: Path) -> None:
        api = FakeKbApi()
        created: list[str] = []
        api.create_kb = lambda name: created.append(name) or f"kb-{len(created)}"  # type: ignore[method-assign]

        def crash(_kb: str, _files: list[tuple[str, bytes, str]]) -> UploadResult:
            raise KeyboardInterrupt

        api.upload = crash  # type: ignore[method-assign]
        with pytest.raises(KeyboardInterrupt):
            _ingestor(api, corpus, tmp_path).prepare(corpus.manifest)
        api.upload = FakeKbApi.upload.__get__(api)  # type: ignore[method-assign]
        assert _ingestor(api, corpus, tmp_path).prepare(corpus.manifest).ingest.kb_id == "kb-1"
        assert len(created) == 1

    def test_failed_batch_is_retried_file_by_file(self, corpus: CorpusView, tmp_path: Path) -> None:
        api = FakeKbApi(fail_batches=True)
        prepared = _ingestor(api, corpus, tmp_path).prepare(corpus.manifest)
        assert len(prepared.ingest.records) == 3
        assert [len(b) for b in api.uploads] == [2, 1, 1, 1]


def _manifest(n: int) -> IngestManifest:
    return IngestManifest(
        system="pipeshub", kb_id="kb-1", corpus_version="v", base_url="u",
        records=[IngestedRecord(record_id=f"r{i}", record_name=f"n{i}", canonical_url=f"https://w/{i}") for i in range(n)],
    )


class TestIndexWaiter:
    def test_waits_reindexes_failures_once_and_reports(self) -> None:
        rounds = iter([
            {"r0": "QUEUED", "r1": "FAILED"},
            {"r0": "COMPLETED", "r1": "IN_PROGRESS"},
            {"r0": "COMPLETED", "r1": "FAILED"},
        ])
        reindexed: list[str] = []
        waiter = IndexWaiter(lambda _kb: next(rounds), reindexed.append, poll_interval_s=0, timeout_s=100, sleep=lambda _s: None)
        report = waiter.wait(_manifest(2), {"https://w/0", "https://w/1"})
        assert reindexed == ["r1"]
        assert (report.gold_indexed, report.gold_total) == (1, 2)
        assert report.unindexed_urls == ["https://w/1"]

    def test_times_out(self) -> None:
        clock = iter([0.0, 5.0, 11.0, 20.0])
        waiter = IndexWaiter(
            lambda _kb: {"r0": "QUEUED"}, lambda _r: None, poll_interval_s=0, timeout_s=10,
            sleep=lambda _s: None, clock=lambda: next(clock),
        )
        with pytest.raises(IndexTimeoutError):
            waiter.wait(_manifest(1), set())


def _collect(frames: list[dict[str, Any]]) -> tuple:
    collector = StreamCollector()
    body = sse(frames).decode()
    for chunk in body.split("\n\n"):
        if chunk.strip():
            event, data = chunk.split("\n", 1)
            collector.feed({"event": event[len("event: "):], "data": data[len("data: "):]})
    return collector.result()


class TestStreamCollector:
    def test_folds_the_stream(self) -> None:
        trace, final, failure = _collect(agui_transcript())
        assert failure is None and trace.finished
        assert trace.conversation_id == "conv-1"
        assert [e.source for e in trace.retrieval_events] == ["prefetch", "tool"]
        assert trace.retrieval_events[1].records[0].block_indices == [0, 5]
        assert trace.retrieval_events[1].known_record_ids == ["rec-9"]
        assert [c.name for c in trace.tool_calls] == ["knowledgegraph__search", "knowledgegraph__fetch_record"]
        assert trace.tool_calls[0].args_json == '{"query": "first lady mother"}'
        assert trace.tool_calls[1].parent_run_id == "run-1"
        assert trace.tool_waves == 1
        assert final.answer == "Jane Ballou [1]" and final.confidence == "High"
        assert final.citations[0].record_id == "rec-1" and final.citations[0].block_nums == [3]

    def test_missing_run_finished_is_a_protocol_failure(self) -> None:
        frames = [f for f in agui_transcript() if f["type"] != "RUN_FINISHED"]
        assert _collect(frames)[2].kind == "protocol"

    def test_run_error_is_reported(self) -> None:
        frames = [*agui_transcript()[:3], {"type": "RUN_ERROR", "message": "LLM quota", "code": "rate_limit"}]
        failure = _collect(frames)[2]
        assert (failure.kind, failure.code) == ("run_error", "rate_limit")


INGEST = IngestManifest(
    system="pipeshub", kb_id="kb-9", corpus_version="v", base_url="u",
    records=[IngestedRecord(record_id="rec-1", record_name="Harriet_Lane--aaaa", canonical_url="https://en.wikipedia.org/wiki/Harriet_Lane")],
)
PREPARED_PH = PreparedCorpus(system="pipeshub", corpus_version="v", ingest=INGEST)


def _adapter(session: FakeSession) -> PipesHubAdapter:
    return PipesHubAdapter(
        "pipeshub", session, None, make_model("gpt-5.6-luna", "openAI", reasoning=True),  # type: ignore[arg-type]
        ToolCallGuard(), current_time=datetime(2024, 10, 15, tzinfo=UTC), stream_timeout_s=60,
    )


class TestPipesHubAdapter:
    def test_request_body_is_internal_search_with_trace_and_nothing_else(self) -> None:
        body = build_stream_body(ITEM, "kb-9", make_model("gpt-5.6-luna", "openAI"), FRAMES_SNAPSHOT)
        assert body == {
            "query": ITEM.prompt, "chatMode": "internal_search", "filters": {"kb": ["kb-9"]},
            "modelKey": "key-gpt-5.6-luna", "modelName": "gpt-5.6-luna", "timezone": "UTC",
            "currentTime": "2024-10-15T00:00:00Z", "includeRetrievalContext": True,
        }

    def test_answer_parses_the_stream(self) -> None:
        session = FakeSession(lambda *_a: FakeStreamResponse(sse(agui_transcript())))
        prediction = _adapter(session).answer(ITEM, PREPARED_PH, 1)
        method, path, kwargs = session.calls[0]
        assert (method, path) == ("POST", "/api/v1/conversations/stream")
        assert kwargs["json"]["chatMode"] == "internal_search" and kwargs["stream"] is True
        assert prediction.error is None and prediction.policy_violations == []
        assert prediction.answer == "Jane Ballou [1]" and len(prediction.trace.retrieval_events) == 2

    def test_web_or_code_tool_calls_are_flagged(self) -> None:
        session = FakeSession(lambda *_a: FakeStreamResponse(sse(agui_transcript(extra_tools=("dynamic__web_search", "run_code")))))
        prediction = _adapter(session).answer(ITEM, PREPARED_PH, 0)
        assert prediction.policy_violations == ["dynamic__web_search", "run_code"]

    def test_retries_once_when_nothing_streamed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("benchmarks.frames.systems.pipeshub.adapter.time.sleep", lambda _s: None)
        responses = iter([FakeStreamResponse(status_code=503), FakeStreamResponse(sse(agui_transcript()))])
        session = FakeSession(lambda *_a: next(responses))
        prediction = _adapter(session).answer(ITEM, PREPARED_PH, 0)
        assert len(session.calls) == 2 and prediction.error is None

    def test_client_error_is_recorded(self) -> None:
        session = FakeSession(lambda *_a: FakeStreamResponse(b'{"error":"bad"}', status_code=400))
        prediction = _adapter(session).answer(ITEM, PREPARED_PH, 0)
        assert prediction.error is not None and prediction.error.kind == "http"

    def test_ranked_search_maps_records_to_articles(self) -> None:
        payload = {"searchResponse": {"searchResults": [
            {"metadata": {"recordId": "rec-1"}}, {"metadata": {"recordId": "rec-1"}}, {"metadata": {"recordId": "unknown"}},
        ]}}
        session = FakeSession(lambda *_a: FakeStreamResponse(json.dumps(payload).encode()))
        ranking = _adapter(session).ranked_search(ITEM, PREPARED_PH, 100)
        assert ranking.ranked_refs == ["https://en.wikipedia.org/wiki/Harriet_Lane"]
        assert session.calls[0][2]["json"]["limit"] == 100

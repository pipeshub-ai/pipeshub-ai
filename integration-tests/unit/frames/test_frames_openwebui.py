"""Open WebUI adapter: what it sends, what it reads back, and how the corpus
gets in -- against a fake client, no server."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from benchmarks.harness.errors import IngestError
from benchmarks.harness.llm.client import ResolvedModel
from benchmarks.harness.models import AskItem, CorpusDocument, CorpusManifest
from benchmarks.harness.systems.base import PreparedCorpus
from benchmarks.harness.systems.openwebui.adapter import (
    OpenWebUIAdapter,
    build_chat_body,
    call_usage_of,
    retrieved_chunks,
)
from benchmarks.harness.systems.openwebui.client import FILE_DONE, FILE_FAILED
from benchmarks.harness.systems.openwebui.ingest import OpenWebUIIngestor, knowledge_name

_SNAPSHOT = datetime(2024, 10, 15, tzinfo=UTC)


def _doc(n: int, tier: str = "gold") -> CorpusDocument:
    return CorpusDocument(
        canonical_url=f"https://en.wikipedia.org/wiki/Article_{n}", title=f"Article {n}", tier=tier,
        filename=f"Article_{n}.html", text_filename=f"Article_{n}.txt", sha256=f"{n:064x}",
    )


def _manifest(docs: list[CorpusDocument]) -> CorpusManifest:
    return CorpusManifest(snapshot=_SNAPSHOT, tier="GD", harness_version="test", documents=docs)


def _corpus_dir(tmp_path: Path, docs: list[CorpusDocument]) -> Path:
    html = tmp_path / "corpus" / "html"
    html.mkdir(parents=True)
    for d in docs:
        (html / d.filename).write_text(f"<html><body><p>{d.title}</p></body></html>")
    return tmp_path / "corpus"


class _FakeClient:
    def __init__(self, statuses: dict[str, list[str]] | None = None) -> None:
        self.knowledge: dict[str, str] = {}
        self.uploads: list[tuple[str, str]] = []
        self._statuses = statuses or {}
        self.chats: list[dict[str, Any]] = []
        self.reply: dict[str, Any] = {}

    def knowledge_id(self, name: str) -> str | None:
        return self.knowledge.get(name)

    def create_knowledge(self, name: str, description: str) -> str:
        self.knowledge[name] = f"kb-{len(self.knowledge) + 1}"
        return self.knowledge[name]

    def upload_file(self, filename: str, content: bytes, mime: str, knowledge_id: str) -> str:
        assert mime == "text/html" and content.startswith(b"<html>")
        self.uploads.append((filename, knowledge_id))
        return f"file-{filename}"

    def file_status(self, file_id: str) -> str:
        queue = self._statuses.get(file_id)
        return queue.pop(0) if queue and len(queue) > 1 else (queue[0] if queue else FILE_DONE)

    def chat(self, body: dict[str, Any]) -> dict[str, Any]:
        self.chats.append(body)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _ingestor(client: _FakeClient, tmp_path: Path, docs: list[CorpusDocument]) -> OpenWebUIIngestor:
    return OpenWebUIIngestor(
        client, system_id="openwebui", base_url="http://owui", corpus_dir=_corpus_dir(tmp_path, docs),
        cache_dir=tmp_path / "cache", upload_workers=2, poll_interval_s=0,
    )


class TestIngest:
    def test_every_document_goes_into_one_knowledge_base(self, tmp_path: Path) -> None:
        docs = [_doc(n) for n in range(5)]
        client = _FakeClient()
        manifest = _manifest(docs)

        prepared = _ingestor(client, tmp_path, docs).prepare(manifest)

        assert list(client.knowledge) == [knowledge_name(manifest.corpus_version)]
        assert sorted(f for f, _kb in client.uploads) == sorted(d.filename for d in docs)
        assert {r.canonical_url for r in prepared.ingest.records} == {d.canonical_url for d in docs}

    def test_a_resumed_run_uploads_only_what_is_missing(self, tmp_path: Path) -> None:
        """An interrupted upload leaves a checkpoint for the same corpus
        version; the next prepare skips what it lists."""
        docs = [_doc(n) for n in range(4)]
        manifest = _manifest(docs)
        client = _FakeClient()
        ingestor = _ingestor(client, tmp_path, docs)
        knowledge_id = client.create_knowledge(knowledge_name(manifest.corpus_version), "")
        ingestor._save(knowledge_id, {d.canonical_url: f"file-{d.filename}" for d in docs[:2]})

        prepared = ingestor.prepare(manifest)

        assert sorted(f for f, _kb in client.uploads) == [docs[2].filename, docs[3].filename]
        assert len(prepared.ingest.records) == 4

    def test_a_different_corpus_gets_its_own_knowledge_base(self, tmp_path: Path) -> None:
        docs = [_doc(n) for n in range(3)]
        client = _FakeClient()
        ingestor = _ingestor(client, tmp_path, docs)

        first = ingestor.prepare(_manifest(docs[:2]))
        second = ingestor.prepare(_manifest(docs))

        assert first.ingest.kb_id != second.ingest.kb_id

    def test_the_report_counts_failed_files_as_unindexed(self, tmp_path: Path) -> None:
        docs = [_doc(0), _doc(1), _doc(2, tier="distractor")]
        client = _FakeClient({
            "file-Article_0.html": ["pending", FILE_DONE],
            "file-Article_1.html": [FILE_FAILED],
        })
        ingestor = _ingestor(client, tmp_path, docs)
        manifest = _manifest(docs)

        report = ingestor.wait_ready(ingestor.prepare(manifest), manifest)

        assert report.status_counts == {FILE_DONE: 2, FILE_FAILED: 1}
        assert (report.gold_total, report.gold_indexed) == (2, 1)
        assert report.unindexed_urls == [docs[1].canonical_url]

    def test_waiting_without_an_ingest_manifest_is_an_error(self, tmp_path: Path) -> None:
        ingestor = _ingestor(_FakeClient(), tmp_path, [_doc(0)])

        with pytest.raises(IngestError):
            ingestor.wait_ready(PreparedCorpus(system="openwebui", corpus_version="v"), _manifest([_doc(0)]))


class TestRequest:
    def test_one_fresh_chat_with_the_knowledge_base_and_snapshot_date(self) -> None:
        body = build_chat_body(AskItem(question_id="1", prompt="Who?"), "kb-1", "gpt-5.6-luna", "high", _SNAPSHOT)

        assert body["model"] == "gpt-5.6-luna"
        assert body["stream"] is False
        assert body["files"] == [{"type": "collection", "id": "kb-1"}]
        assert body["messages"][-1] == {"role": "user", "content": "Who?"}
        assert "2024-10-15" in body["messages"][0]["content"]
        assert body["reasoning"] == {"effort": "high"}
        assert not {"chat_id", "session_id", "background_tasks"} & set(body)

    def test_no_reasoning_field_without_an_effort(self) -> None:
        body = build_chat_body(AskItem(question_id="1", prompt="Who?"), "kb-1", "m", None, _SNAPSHOT)

        assert "reasoning" not in body


class TestResponse:
    def test_sources_map_to_corpus_urls_in_prompt_order(self) -> None:
        body = {"sources": [
            {"metadata": [{"file_id": "f2"}, {"file_id": "f1"}], "distances": [0.9, 0.8]},
            {"metadata": [{"file_id": "unknown"}, {"file_id": "f2"}], "distances": [0.7, 0.6]},
        ]}

        chunks = retrieved_chunks(body, {"f1": "url-1", "f2": "url-2"})

        assert [c.url for c in chunks] == ["url-2", "url-1", "url-2"]
        assert chunks[0].score == pytest.approx(0.9)

    def test_usage_in_either_api_shape(self) -> None:
        chat = call_usage_of({"usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                        "prompt_tokens_details": {"cached_tokens": 3}}})
        responses = call_usage_of({"usage": {"input_tokens": 7, "output_tokens": 2}})

        assert (chat[0].input_tokens, chat[0].output_tokens, chat[0].cached_tokens) == (10, 5, 3)
        assert (responses[0].input_tokens, responses[0].output_tokens) == (7, 2)
        assert call_usage_of({}) == []


class TestAnswer:
    def _adapter(self, client: _FakeClient) -> OpenWebUIAdapter:
        model = ResolvedModel(model_key="answerer", provider="azureOpenAI", model_name="gpt-5.6-luna",
                              reasoning_effort="high")
        return OpenWebUIAdapter("openwebui", client, None, model,  # type: ignore[arg-type]
                                reasoning_effort="high", current_time=_SNAPSHOT, price=None)

    def _prepared(self, tmp_path: Path) -> PreparedCorpus:
        docs = [_doc(0), _doc(1)]
        return _ingestor(_FakeClient(), tmp_path, docs).prepare(_manifest(docs))

    def test_answer_context_and_usage_are_recorded(self, tmp_path: Path) -> None:
        prepared = self._prepared(tmp_path)
        client = _FakeClient()
        client.reply = {
            "choices": [{"message": {"content": "Article 1."}}],
            "sources": [{"metadata": [{"file_id": "file-Article_1.html"}, {"file_id": "file-Article_1.html"}]}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }

        prediction = self._adapter(client).answer(AskItem(question_id="7", prompt="Which?"), prepared, 0)

        assert prediction.answer == "Article 1."
        assert prediction.context_urls == [_doc(1).canonical_url]
        assert len(prediction.retrieved) == 2
        assert (prediction.prompt_tokens, prediction.completion_tokens) == (100, 20)
        assert client.chats[0]["files"] == [{"type": "collection", "id": prepared.ingest.kb_id}]

    def test_a_failed_call_is_a_recorded_failure_not_a_crash(self, tmp_path: Path) -> None:
        prepared = self._prepared(tmp_path)
        client = _FakeClient()
        client.reply = RuntimeError("502 Bad Gateway")  # type: ignore[assignment]

        prediction = self._adapter(client).answer(AskItem(question_id="7", prompt="Which?"), prepared, 0)

        assert prediction.error is not None and prediction.error.kind == "http"
        assert prediction.answer == ""


class TestModelsWithoutPipesHub:
    """A run with no PipesHub-backed system must not need PipesHub up just to
    name its answer and judge models; a run with one still resolves them
    through PipesHub's registry."""

    @staticmethod
    def _services(systems: list[dict]):  # noqa: ANN205
        from benchmarks.harness.config import RunConfig
        from benchmarks.harness.credentials import Credentials
        from benchmarks.harness.services import Services

        return Services(
            RunConfig.model_validate({
                "run_name": "t", "systems": systems,
                "answerer": {"model": "gpt-5.6-luna", "provider": "azureOpenAI", "is_reasoning": True,
                             "reasoning_effort": "high"},
                "grading": {"primary": {"model": "j", "provider": "anthropic"}},
            }),
            Credentials.from_env({}),
        )

    def test_a_competitor_run_resolves_models_from_its_config(self) -> None:
        services = self._services([{"kind": "oracle"}, {"kind": "openwebui"}])
        services.__dict__["resolver"] = None  # any registry lookup would fail

        answerer = services.model(services.config.answerer)
        judge = services.judge_model(services.config.grading.primary)

        assert (answerer.model_name, answerer.provider, answerer.reasoning_effort) == ("gpt-5.6-luna", "azureOpenAI", "high")
        assert judge.model_name == "j"

    @pytest.mark.parametrize("systems", [
        [{"kind": "pipeshub"}],
        [{"kind": "advanced_rag", "options": {"index": "pipeshub"}}],
    ])
    def test_a_run_that_reads_pipeshub_still_asks_its_registry(self, systems: list[dict]) -> None:
        services = self._services(systems)
        asked: list[object] = []

        class _Registry:
            def resolve(self, selector: object) -> object:
                asked.append(selector)
                return "from-registry"

        services.__dict__["resolver"] = _Registry()

        assert services.model(services.config.answerer) == "from-registry"
        assert asked

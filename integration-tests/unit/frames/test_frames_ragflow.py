"""RAGFlow adapter: ingestion, parsing status and answers, against a fake
client, no server."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from benchmarks.harness.errors import IngestError
from benchmarks.harness.models import INDEXED, AskItem, CorpusDocument, CorpusManifest
from benchmarks.harness.systems.ragflow.adapter import RagflowAdapter, retrieved_chunks
from benchmarks.harness.systems.ragflow.ingest import RagflowIngestor, dataset_name

_SNAPSHOT = datetime(2024, 10, 15, tzinfo=UTC)
_DATASET = {"embedding_model": "text-embedding-3-small@azure@Azure-OpenAI", "chunk_method": "naive"}
_CHAT = {"llm_id": "gpt-5.6-luna@azure@Azure-OpenAI", "top_n": 50, "similarity_threshold": 0, "rerank_id": ""}


def _doc(n: int, tier: str = "gold") -> CorpusDocument:
    return CorpusDocument(
        canonical_url=f"https://en.wikipedia.org/wiki/Article_{n}", title=f"Article {n}", tier=tier,
        filename=f"Article_{n}.html", text_filename=f"Article_{n}.txt", sha256=f"{n:064x}",
    )


def _manifest(docs: list[CorpusDocument]) -> CorpusManifest:
    return CorpusManifest(snapshot=_SNAPSHOT, tier="GD", harness_version="test", documents=docs)


class _FakeClient:
    def __init__(self, runs: dict[str, list[str]] | None = None, reject: set[str] | None = None) -> None:
        self.datasets: dict[str, tuple[str, dict[str, Any]]] = {}
        self.uploads: list[list[str]] = []
        self.parsed: list[str] = []
        self._runs = runs or {}
        self._reject = reject or set()
        self.reply: Any = {}
        self.chats: dict[str, dict[str, Any]] = {}
        self.asked: list[str] = []

    def dataset_id(self, name: str) -> str | None:
        return self.datasets[name][0] if name in self.datasets else None

    def create_dataset(self, body: dict[str, Any]) -> str:
        self.datasets[body["name"]] = (f"ds-{len(self.datasets) + 1}", body)
        return self.datasets[body["name"]][0]

    def upload(self, dataset_id: str, files: list[tuple[str, bytes, str]]) -> dict[str, str]:
        self.uploads.append([name for name, _c, _m in files])
        return {name: f"doc-{name}" for name, _c, _m in files if name not in self._reject}

    def parse(self, dataset_id: str, document_ids: list[str]) -> None:
        self.parsed.extend(document_ids)

    def documents(self, dataset_id: str, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        assert page_size <= 100, "RAGFlow rejects pages over 100"
        docs = []
        for doc_id in self.parsed:
            queue = self._runs.get(doc_id, ["DONE"])
            run = queue.pop(0) if len(queue) > 1 else queue[0]
            docs.append({"id": doc_id, "run": run})
        return docs[(page - 1) * page_size: page * page_size], len(docs)

    def chat_id(self, name: str) -> str | None:
        return next((cid for cid, c in self.chats.items() if c["name"] == name), None)

    def create_chat(self, body: dict[str, Any]) -> str:
        chat_id = f"chat-{len(self.chats) + 1}"
        self.chats[chat_id] = {**body, "prompt_config": {"system": "Default prompt {knowledge}", "quote": True}}
        return chat_id

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        return self.chats[chat_id]

    def update_chat(self, chat_id: str, body: dict[str, Any]) -> None:
        self.chats[chat_id].update(body)

    def complete(self, chat_id: str, question: str) -> dict[str, Any]:
        self.asked.append(chat_id)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _ingestor(client: _FakeClient, tmp_path: Path, docs: list[CorpusDocument], **kwargs: Any) -> RagflowIngestor:  # noqa: ANN401
    html = tmp_path / "corpus" / "html"
    html.mkdir(parents=True, exist_ok=True)
    for d in docs:
        (html / d.filename).write_text(f"<html><body><p>{d.title}</p></body></html>")
    return RagflowIngestor(
        client, system_id="ragflow", base_url="http://rf", corpus_dir=tmp_path / "corpus",
        cache_dir=tmp_path / "cache", dataset_config=_DATASET, poll_interval_s=0, **kwargs,
    )


class TestIngest:
    def test_documents_are_uploaded_in_batches_and_parsed(self, tmp_path: Path) -> None:
        docs = [_doc(n) for n in range(5)]
        client = _FakeClient()
        manifest = _manifest(docs)

        prepared = _ingestor(client, tmp_path, docs, batch_size=2, upload_workers=1).prepare(manifest)

        name = dataset_name(manifest.corpus_version)
        assert client.datasets[name][1] == {**_DATASET, "name": name}
        assert [len(b) for b in client.uploads] == [2, 2, 1]
        assert sorted(client.parsed) == sorted(f"doc-{d.filename}" for d in docs)
        assert {r.record_id for r in prepared.ingest.records} == set(client.parsed)

    def test_a_resumed_run_uploads_only_what_is_missing(self, tmp_path: Path) -> None:
        docs = [_doc(n) for n in range(3)]
        manifest = _manifest(docs)
        client = _FakeClient()
        ingestor = _ingestor(client, tmp_path, docs)
        dataset_id = client.create_dataset({"name": dataset_name(manifest.corpus_version)})
        ingestor._save(dataset_id, {docs[0].canonical_url: "doc-Article_0.html"})

        ingestor.prepare(manifest)

        assert sorted(n for batch in client.uploads for n in batch) == [docs[1].filename, docs[2].filename]

    def test_a_file_ragflow_did_not_accept_fails_the_ingest(self, tmp_path: Path) -> None:
        docs = [_doc(0), _doc(1)]
        client = _FakeClient(reject={docs[1].filename})

        with pytest.raises(IngestError):
            _ingestor(client, tmp_path, docs).prepare(_manifest(docs))

    def test_statuses_are_read_across_pages(self, tmp_path: Path) -> None:
        docs = [_doc(n) for n in range(230)]
        client = _FakeClient()
        ingestor = _ingestor(client, tmp_path, docs, batch_size=100)
        manifest = _manifest(docs)

        report = ingestor.wait_ready(ingestor.prepare(manifest), manifest)

        assert report.total == 230 and report.status_counts == {INDEXED: 230}
        assert report.indexed_ratio(230) == 1.0, "the pipeline gate reads the shared status key"

    def test_the_report_waits_for_parsing_and_counts_failures(self, tmp_path: Path) -> None:
        docs = [_doc(0), _doc(1), _doc(2, tier="distractor")]
        client = _FakeClient(runs={
            "doc-Article_0.html": ["RUNNING", "DONE"],
            "doc-Article_1.html": ["FAIL"],
        })
        ingestor = _ingestor(client, tmp_path, docs)
        manifest = _manifest(docs)

        report = ingestor.wait_ready(ingestor.prepare(manifest), manifest)

        assert report.status_counts == {INDEXED: 2, "FAIL": 1}
        assert (report.gold_total, report.gold_indexed) == (2, 1)
        assert report.unindexed_urls == [docs[1].canonical_url]


class TestAnswer:
    def test_every_chunk_given_to_the_model_counts_as_context(self) -> None:
        data = {"reference": {
            "chunks": [
                {"document_id": "d2", "similarity": 0.9},
                {"document_id": "d1", "similarity": 0.7},
                {"document_id": "elsewhere", "similarity": 0.5},
            ],
            "doc_aggs": [{"doc_id": "d2"}],
        }}

        chunks = retrieved_chunks(data, {"d1": "url-1", "d2": "url-2"})

        assert [c.url for c in chunks] == ["url-2", "url-1"]

    def test_no_reference_means_no_context(self) -> None:
        assert retrieved_chunks({"reference": []}, {}) == []

    def test_answer_and_context_are_recorded(self, tmp_path: Path) -> None:
        docs = [_doc(0), _doc(1)]
        client = _FakeClient()
        ingestor = _ingestor(client, tmp_path, docs)
        prepared = ingestor.prepare(_manifest(docs))
        client.reply = {"answer": "Article 1 [ID:0].", "reference": {"chunks": [{"document_id": "doc-Article_1.html"}]}}

        prediction = RagflowAdapter("ragflow", client, ingestor, chat_config=_CHAT, current_time=_SNAPSHOT).answer(
            AskItem(question_id="3", prompt="Which?"), prepared, 0,
        )

        assert prediction.answer == "Article 1 [ID:0]."
        assert prediction.context_urls == [docs[1].canonical_url]
        assert prediction.prompt_tokens is None, "RAGFlow reports no usage; it is not estimated"

    def test_a_failed_call_is_a_recorded_failure(self, tmp_path: Path) -> None:
        docs = [_doc(0)]
        client = _FakeClient()
        ingestor = _ingestor(client, tmp_path, docs)
        prepared = ingestor.prepare(_manifest(docs))
        client.reply = RuntimeError("code 100: model error")

        prediction = RagflowAdapter("ragflow", client, ingestor, chat_config=_CHAT, current_time=_SNAPSHOT).answer(
            AskItem(question_id="3", prompt="Which?"), prepared, 0,
        )

        assert prediction.error is not None and prediction.answer == ""


class TestChat:
    def test_one_chat_is_created_bound_to_the_dataset_with_the_snapshot_date(self, tmp_path: Path) -> None:
        docs = [_doc(0)]
        client = _FakeClient()
        ingestor = _ingestor(client, tmp_path, docs)
        prepared = ingestor.prepare(_manifest(docs))
        client.reply = {"answer": "x", "reference": {"chunks": []}}
        adapter = RagflowAdapter("ragflow", client, ingestor, chat_config=_CHAT, current_time=_SNAPSHOT)

        for n in range(3):
            adapter.answer(AskItem(question_id=str(n), prompt="q"), prepared, 0)

        [(chat_id, chat)] = client.chats.items()
        assert chat["dataset_ids"] == [prepared.ingest.kb_id]
        assert chat["top_n"] == 50 and chat["llm_id"] == _CHAT["llm_id"]
        assert chat["prompt_config"]["system"].startswith("Default prompt {knowledge}")
        assert chat["prompt_config"]["system"].endswith("Today's date is 2024-10-15.")
        assert chat["prompt_config"]["quote"] is True, "the rest of the prompt config is kept"
        assert client.asked == [chat_id] * 3

    def test_an_existing_chat_is_reused_unchanged(self, tmp_path: Path) -> None:
        docs = [_doc(0)]
        client = _FakeClient()
        ingestor = _ingestor(client, tmp_path, docs)
        prepared = ingestor.prepare(_manifest(docs))
        from benchmarks.harness.systems.ragflow.adapter import chat_name

        existing = client.create_chat({"name": chat_name(prepared.ingest.kb_id)})
        client.reply = {"answer": "x", "reference": {"chunks": []}}

        RagflowAdapter("ragflow", client, ingestor, chat_config=_CHAT, current_time=_SNAPSHOT).answer(
            AskItem(question_id="1", prompt="q"), prepared, 0,
        )

        assert list(client.chats) == [existing]
        assert "Today" not in client.chats[existing]["prompt_config"]["system"]

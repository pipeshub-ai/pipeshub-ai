"""The standard-index build must not trust a checkpoint that outlived its collection."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from benchmarks.harness.config import StandardIndexConfig
from benchmarks.harness.systems.rag import standard_index
from benchmarks.harness.systems.rag.standard_index import StandardIndexIngestor, collection_name


class _FakeQdrant:
    def __init__(self, *, exists: bool) -> None:
        self.exists = exists
        self.created: list[str] = []

    def collection_exists(self, _name: str) -> bool:
        return self.exists

    def create_collection(self, collection_name: str, **_kwargs: Any) -> None:
        self.created.append(collection_name)
        self.exists = True

    def create_payload_index(self, *_args: Any, **_kwargs: Any) -> None:
        pass


class _FakeLLM:
    def embed(self, _model: Any, texts: list[str]) -> Any:
        return SimpleNamespace(vectors=[[0.0, 1.0] for _ in texts])


def _manifest() -> Any:
    docs = [SimpleNamespace(canonical_url=f"https://kb.test/{n}", title=n, tier="gold") for n in ("a", "b")]
    return SimpleNamespace(corpus_version="v" * 64, documents=docs)


def _ingestor(tmp_path: Path, client: _FakeQdrant, indexed: list[str]) -> StandardIndexIngestor:
    corpus = SimpleNamespace(text=lambda url: f"text of {url}")
    ingestor = StandardIndexIngestor(
        client, _FakeLLM(), SimpleNamespace(), corpus, StandardIndexConfig(),
        system_id="naive-std", cache_dir=tmp_path,
    )
    ingestor._index_batch = lambda _collection, batch: indexed.extend(url for url, *_ in batch)  # type: ignore[method-assign]
    return ingestor


def _write_checkpoint(tmp_path: Path, urls: list[str]) -> Path:
    path = tmp_path / "standard_index" / f"{collection_name(StandardIndexConfig(), 'v' * 64)}.done.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(urls))
    return path


def test_a_recreated_collection_is_rebuilt_despite_an_old_checkpoint(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(standard_index, "chunk_text", lambda text, *_: [text])
    _write_checkpoint(tmp_path, ["https://kb.test/a", "https://kb.test/b"])
    client, indexed = _FakeQdrant(exists=False), []

    prepared = _ingestor(tmp_path, client, indexed).prepare(_manifest())

    assert client.created
    assert sorted(indexed) == ["https://kb.test/a", "https://kb.test/b"]
    assert len(prepared.ingest.records) == 2


def test_an_existing_collection_resumes_from_its_checkpoint(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(standard_index, "chunk_text", lambda text, *_: [text])
    _write_checkpoint(tmp_path, ["https://kb.test/a"])
    client, indexed = _FakeQdrant(exists=True), []

    _ingestor(tmp_path, client, indexed).prepare(_manifest())

    assert not client.created
    assert indexed == ["https://kb.test/b"]

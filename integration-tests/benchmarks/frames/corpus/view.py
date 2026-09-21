"""Read-side view of a built corpus, shared by baselines and scoring."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from benchmarks.frames.corpus.manifest import read_article_text
from benchmarks.frames.dataset.urls import normalize_wiki_url
from benchmarks.frames.models import CorpusDocument, CorpusManifest


class CorpusView:
    def __init__(self, corpus_dir: Path, manifest: CorpusManifest) -> None:
        self.corpus_dir = corpus_dir
        self.manifest = manifest
        self._text = lru_cache(maxsize=4096)(self._read_text)

    def _read_text(self, canonical_url: str) -> str:
        document = self.manifest.document(canonical_url)
        return read_article_text(self.corpus_dir, document.text_filename) if document else ""

    def text(self, canonical_url: str) -> str:
        return self._text(canonical_url)

    def document(self, canonical_url: str) -> CorpusDocument | None:
        return self.manifest.document(canonical_url)

    def resolve_refs(self, dataset_urls: Sequence[str]) -> list[str]:
        """Dataset links -> canonical URLs of the pinned articles, de-duplicated.
        Links that could not be resolved at corpus build time are dropped."""
        resolved = (self.manifest.gold_aliases.get(normalize_wiki_url(url).key) for url in dataset_urls)
        return list(dict.fromkeys(url for url in resolved if url))

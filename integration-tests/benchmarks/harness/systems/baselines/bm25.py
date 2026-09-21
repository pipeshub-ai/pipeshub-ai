"""S1 BM25: the paper's BM25-R baseline over the same corpus (whole articles,
top `n_docs`, default 4), plus a ranked list for retrieval recall@k."""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from rank_bm25 import BM25Okapi

from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.llm.client import LLMClient, ResolvedModel
from benchmarks.harness.models import AskItem, RankedList
from benchmarks.harness.systems.base import AdapterCapabilities, PreparedCorpus, RankedRetriever
from benchmarks.harness.systems.baselines.answering import BaselineAnswerer, ContextDocument

logger = logging.getLogger(__name__)

DEFAULT_N_DOCS = 4
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class BM25Index:
    """Built lazily on first query; thread-safe for concurrent askers."""

    def __init__(self, corpus: CorpusView) -> None:
        self._corpus = corpus
        self._urls = [d.canonical_url for d in corpus.manifest.documents]
        self._index: BM25Okapi | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> BM25Okapi:
        with self._lock:
            if self._index is None:
                logger.info("building BM25 index over %d articles", len(self._urls))
                self._index = BM25Okapi([tokenize(self._corpus.text(url)) for url in self._urls])
            return self._index

    def search(self, query: str, k: int) -> list[str]:
        scores = self._ensure().get_scores(tokenize(query))
        order = sorted(range(len(self._urls)), key=lambda i: (-scores[i], self._urls[i]))
        return [self._urls[i] for i in order[:k]]


class Bm25Answerer(BaselineAnswerer):
    capabilities = AdapterCapabilities(ranked_search=True)

    def __init__(
        self, system_id: str, llm: LLMClient, model: ResolvedModel, corpus: CorpusView,
        *, n_docs: int = DEFAULT_N_DOCS, index: BM25Index | None = None, **answerer: Any,  # noqa: ANN401
    ) -> None:
        super().__init__(system_id, llm, model, **answerer)
        self._corpus = corpus
        self._n_docs = n_docs
        self._index = index or BM25Index(corpus)

    def documents_for(self, item: AskItem) -> list[ContextDocument]:
        docs = []
        for url in self._index.search(item.prompt, self._n_docs):
            document = self._corpus.document(url)
            if document is not None:
                docs.append(ContextDocument(url, document.title, self._corpus.text(url)))
        return docs

    def ranked_search(self, item: AskItem, prepared: PreparedCorpus, k: int) -> RankedList:
        return RankedList(system=self.system_id, question_id=item.question_id, ranked_refs=self._index.search(item.prompt, k))

    def retriever(self) -> RankedRetriever | None:
        return self

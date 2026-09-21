"""S4 oracle: every gold article in the prompt — the ceiling for this model
when retrieval is perfect."""

from __future__ import annotations

from typing import Any

from benchmarks.frames.corpus.view import CorpusView
from benchmarks.frames.llm.client import LLMClient, ResolvedModel
from benchmarks.frames.models import AskItem
from benchmarks.frames.systems.base import AdapterCapabilities
from benchmarks.frames.systems.baselines.answering import BaselineAnswerer, ContextDocument


class OracleAnswerer(BaselineAnswerer):
    # The upper bound IS the gold articles; it is the only adapter granted them.
    capabilities = AdapterCapabilities(needs_gold_refs=True)

    def __init__(self, system_id: str, llm: LLMClient, model: ResolvedModel, corpus: CorpusView, **answerer: Any) -> None:  # noqa: ANN401
        super().__init__(system_id, llm, model, **answerer)
        self._corpus = corpus

    def documents_for(self, item: AskItem) -> list[ContextDocument]:
        docs = []
        for url in self._corpus.resolve_refs(item.gold_refs):
            document = self._corpus.document(url)
            if document is not None:
                docs.append(ContextDocument(url, document.title, self._corpus.text(url)))
        return docs

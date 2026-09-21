"""Read-side view of a built corpus, shared by baselines and scoring."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from collections.abc import Callable

from benchmarks.harness.corpus.manifest import read_article_text
from benchmarks.harness.models import CorpusDocument, CorpusManifest


class CorpusView:
    def __init__(
        self,
        corpus_dir: Path,
        manifest: CorpusManifest,
        normalize_ref: Callable[[str], str] = lambda ref: ref,
    ) -> None:
        self.corpus_dir = corpus_dir
        self.manifest = manifest
        # Only the dataset knows how two refs that name the same document
        # differ; the default suits a dataset whose refs are already keys.
        self._normalize_ref = normalize_ref
        self._text = lru_cache(maxsize=4096)(self._read_text)

    def _read_text(self, canonical_url: str) -> str:
        document = self.manifest.document(canonical_url)
        return read_article_text(self.corpus_dir, document.text_filename) if document else ""

    def text(self, canonical_url: str) -> str:
        return self._text(canonical_url)

    def document(self, canonical_url: str) -> CorpusDocument | None:
        return self.manifest.document(canonical_url)

    def resolve_refs(self, refs: Sequence[str]) -> list[str]:
        """Dataset refs -> canonical URLs of the pinned documents, de-duplicated.
        Refs that could not be resolved at corpus build time are dropped."""
        resolved = (self.manifest.gold_aliases.get(self._normalize_ref(ref)) for ref in refs)
        return list(dict.fromkeys(url for url in resolved if url))

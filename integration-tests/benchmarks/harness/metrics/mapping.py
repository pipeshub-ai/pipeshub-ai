"""Mapping whatever a system reports (record ids, names, events, citations)
back to canonical Wikipedia article URLs — the unit every metric counts in."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from benchmarks.harness.corpus.manifest import record_name
from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.models import Citation, IngestManifest, RetrievalEvent


class ArticleResolver:
    def __init__(self, corpus: CorpusView, ingest: IngestManifest | None = None) -> None:
        self._corpus = corpus
        self._by_record = {r.record_id: r.canonical_url for r in ingest.records} if ingest else {}
        self._by_name = {record_name(d.filename): d.canonical_url for d in corpus.manifest.documents}

    @property
    def known_record_ids(self) -> set[str]:
        return set(self._by_record)

    def url_for(self, record_id: str | None, name: str | None = None) -> str | None:
        if record_id and record_id in self._by_record:
            return self._by_record[record_id]
        return self._by_name.get(name) if name else None

    def gold(self, dataset_urls: Sequence[str]) -> list[str]:
        return self._corpus.resolve_refs(dataset_urls)

    def context_urls(self, events: Iterable[RetrievalEvent]) -> set[str]:
        """Articles with at least one block (or a rendered fetch) in the model's context."""
        return {
            url for event in events for record in event.records
            if record.reached_model and (url := self.url_for(record.record_id, record.record_name))
        }

    def surfaced_urls(self, events: Iterable[RetrievalEvent]) -> set[str]:
        """Articles the agent saw at all, including ids surfaced without content."""
        events = list(events)
        surfaced = {
            url for event in events for record in event.records
            if (url := self.url_for(record.record_id, record.record_name))
        }
        surfaced |= {url for event in events for rid in event.known_record_ids if (url := self.url_for(rid))}
        return surfaced

    def unrendered_fetch_urls(self, events: Iterable[RetrievalEvent]) -> set[str]:
        """Articles fetched in full whose blocks did not fit the model's budget."""
        return {
            url for event in events for record in event.records
            if record.fetched and not record.reached_model
            and (url := self.url_for(record.record_id, record.record_name))
        }

    def citation_urls(self, citations: Iterable[Citation]) -> list[str]:
        urls = (self.url_for(c.record_id, c.record_name) for c in citations)
        return list(dict.fromkeys(url for url in urls if url))

"""`CorpusBuilder`: gold (and optionally distractor) articles pinned to the
revision in force at the snapshot, cleaned and written with a manifest.

Resumable: each finished article is appended to a partial log, and a re-run
only fetches what is missing. Failures are recorded, not raised, until the
share of unresolvable gold articles exceeds the configured threshold.
"""

from __future__ import annotations

import hashlib
import logging
import random
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

import requests
from pydantic import BaseModel, ConfigDict

from benchmarks.frames.concurrency import run_parallel
from benchmarks.frames.corpus.cleaner import clean_article_html
from benchmarks.frames.corpus.manifest import HTML_DIR, TEXT_DIR, safe_filename, save_manifest, text_filename
from benchmarks.frames.corpus.mediawiki import ParsedPage, RevisionRef
from benchmarks.frames.dataset.urls import WikiRef, canonical_article_url, normalize_wiki_url
from benchmarks.frames.errors import CorpusError, FramesError
from benchmarks.frames.models import CorpusDocument, CorpusManifest
from benchmarks.frames.store import atomic_write_text, read_jsonl

logger = logging.getLogger(__name__)

PARTIAL_FILE = "documents.partial.jsonl"
Tier = Literal["gold", "distractor"]


class ArticleSource(Protocol):
    def revision_at(self, title: str, snapshot: datetime) -> RevisionRef | None: ...
    def parse_revision(self, revid: int) -> ParsedPage: ...
    def search_title(self, query: str) -> str | None: ...
    def resolve_short_link(self, url: str) -> str: ...


ClientFactory = Callable[[str], ArticleSource]


@dataclass(frozen=True)
class _Target:
    host: str
    title: str
    tier: Tier

    @property
    def key(self) -> str:
        return f"{self.tier}:{self.host}/{self.title}"


class _PartialEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    document: CorpusDocument | None = None
    error: str | None = None


class CorpusBuilder:
    def __init__(
        self,
        client_factory: ClientFactory,
        out_dir: Path,
        *,
        snapshot: datetime,
        workers: int,
        harness_version: str,
    ) -> None:
        self._client_factory = client_factory
        self._out_dir = out_dir
        self._snapshot = snapshot
        self._workers = workers
        self._harness_version = harness_version
        self._local = threading.local()
        self._write_lock = threading.Lock()

    def build(
        self,
        gold_refs: Sequence[str],
        *,
        tier: Literal["G", "GD"],
        distractor_count: int,
        seed: int,
        max_failed_gold_ratio: float,
    ) -> CorpusManifest:
        done = self._load_partial()
        # Ref -> wiki semantics belongs here, not in the pipeline stage: a
        # dataset that is not Wikipedia brings its own CorpusSource.
        refs = list({r.key: r for r in (normalize_wiki_url(u) for u in gold_refs)}.values())
        targets, ref_targets, unresolved = self._resolve_refs(refs)
        gold = self._materialise(targets, done)
        aliases = {ref_key: gold[t.key].canonical_url for ref_key, t in ref_targets.items() if t.key in gold}
        unresolved += sorted(ref_key for ref_key, t in ref_targets.items() if t.key not in gold)
        self._check_gold_coverage(len(unresolved), len(refs), max_failed_gold_ratio)
        documents = list({d.canonical_url: d for d in gold.values()}.values())
        if tier == "GD":
            documents += self._distractors(documents, distractor_count, seed, done)
        manifest = CorpusManifest(
            snapshot=self._snapshot, tier=tier, harness_version=self._harness_version,
            documents=sorted(documents, key=lambda d: d.canonical_url),
            gold_aliases=aliases, unresolved=unresolved,
        )
        save_manifest(self._out_dir, manifest)
        logger.info(
            "corpus %s: %d documents, %d unresolved gold links, version %s",
            tier, len(manifest.documents), len(unresolved), manifest.corpus_version[:12],
        )
        return manifest

    def _client(self, host: str) -> ArticleSource:
        clients = getattr(self._local, "clients", None)
        if clients is None:
            clients = self._local.clients = {}
        if host not in clients:
            clients[host] = self._client_factory(host)
        return clients[host]

    def _resolve_refs(
        self, refs: Sequence[WikiRef],
    ) -> tuple[list[_Target], dict[str, _Target], list[str]]:
        by_ref: dict[str, _Target] = {}
        unresolved: list[str] = []
        for ref in refs:
            target = self._target_for(ref)
            if target is None:
                unresolved.append(ref.key)
            else:
                by_ref[ref.key] = target
        return list(dict.fromkeys(by_ref.values())), by_ref, unresolved

    def _target_for(self, ref: WikiRef) -> _Target | None:
        try:
            if ref.kind == "short_link":
                ref = normalize_wiki_url(self._client(ref.host).resolve_short_link(ref.raw))
            if ref.kind == "search":
                title = self._client(ref.host).search_title(ref.title)
                return _Target(ref.host, title, "gold") if title else None
        except (FramesError, requests.RequestException) as exc:
            logger.warning("could not resolve %s: %s", ref.raw, exc)
            return None
        return _Target(ref.host, ref.title, "gold") if ref.kind == "article" and ref.title else None

    def _check_gold_coverage(self, failed: int, total: int, max_ratio: float) -> None:
        if total and failed / total > max_ratio:
            raise CorpusError(f"{failed}/{total} gold links unresolved (limit {max_ratio:.1%})")

    def _distractors(
        self, gold: list[CorpusDocument], count: int, seed: int, done: dict[str, _PartialEntry],
    ) -> list[CorpusDocument]:
        gold_refs = {d.canonical_url for d in gold}
        pool = sorted({url for d in gold for url in d.outlinks} - gold_refs)
        picked = random.Random(seed).sample(pool, min(count, len(pool)))
        targets = []
        for url in picked:
            ref = normalize_wiki_url(url)
            targets.append(_Target(ref.host, ref.title, "distractor"))
        found = self._materialise(targets, done)
        return [d for d in {d.canonical_url: d for d in found.values()}.values() if d.canonical_url not in gold_refs]

    def _load_partial(self) -> dict[str, _PartialEntry]:
        entries = read_jsonl(self._out_dir / PARTIAL_FILE, _PartialEntry)
        return {
            e.key: e for e in entries
            if e.document is not None and (self._out_dir / HTML_DIR / e.document.filename).exists()
        }

    def _materialise(self, targets: Sequence[_Target], done: dict[str, _PartialEntry]) -> dict[str, CorpusDocument]:
        results = {t.key: done[t.key].document for t in targets if t.key in done}
        todo = [t for t in targets if t.key not in done]
        logger.info("fetching %d articles (%d already on disk)", len(todo), len(results))

        def _record(target: _Target, entry: _PartialEntry) -> None:
            with self._write_lock:
                with (self._out_dir / PARTIAL_FILE).open("a", encoding="utf-8") as handle:
                    handle.write(entry.model_dump_json() + "\n")
            if entry.document is not None:
                results[target.key] = entry.document
                done[target.key] = entry

        run_parallel(todo, self._fetch_one, workers=self._workers, on_result=_record)
        return {key: doc for key, doc in results.items() if doc is not None}

    def _fetch_one(self, target: _Target) -> _PartialEntry:
        try:
            client = self._client(target.host)
            revision = client.revision_at(target.title, self._snapshot)
            if revision is None:
                return _PartialEntry(key=target.key, error="page does not exist")
            return _PartialEntry(key=target.key, document=self._write_document(target, revision, client))
        except (FramesError, requests.RequestException, KeyError, ValueError) as exc:
            logger.warning("failed to fetch %s: %s", target.key, exc)
            return _PartialEntry(key=target.key, error=str(exc)[:500])

    def _write_document(self, target: _Target, revision: RevisionRef, client: ArticleSource) -> CorpusDocument:
        page = client.parse_revision(revision.revid)
        url = canonical_article_url(target.host, revision.title)
        cleaned = clean_article_html(
            page.html, title=revision.title, host=target.host, source_url=url, revid=revision.revid,
        )
        html_name = safe_filename(revision.title, url)
        atomic_write_text(self._out_dir / HTML_DIR / html_name, cleaned.html)
        atomic_write_text(self._out_dir / TEXT_DIR / text_filename(html_name), cleaned.text)
        return CorpusDocument(
            canonical_url=url, title=revision.title, tier=target.tier,
            filename=html_name, text_filename=text_filename(html_name),
            sha256=hashlib.sha256(cleaned.html.encode()).hexdigest(),
            # A MediaWiki revision id pins the article; `source` carries the rest
            # of what identifies it on *this* source and nothing generic needs.
            revision=str(revision.revid),
            source={
                "host": target.host,
                "page_id": revision.page_id,
                "rev_timestamp": revision.timestamp.isoformat(),
                "post_snapshot_revision": revision.post_snapshot,
            },
            outlinks=tuple(cleaned.outlinks),
        )

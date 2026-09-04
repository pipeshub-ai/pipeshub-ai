"""`SemanticSkillIndex`: agent_loop_lib's `SkillIndex` backed by
`RetrievalService`'s embedder, falling back to the exact keyword scoring
`FilesystemSkillIndex` uses (`text_scoring.py`) when no embedder is
configured (e.g. no `embedding` block in AI Models config, or the config
lookup itself fails) — `skill_search` never regresses to zero results just
because embeddings aren't set up.

Hybrid score = max(semantic, keyword) per skill, per the plan: an exact
name/tag match should never rank below a vague semantic neighbor. Vectors
are held in memory (`{name: vector}` — a catalog is hundreds of skills per
org, not millions) and recomputed incrementally on `add_entry`/
`update_entry`, in bulk on `rebuild`. A future Qdrant-backed variant is a
drop-in replacement — `SkillIndex` is an ABC — once catalogs grow past
what an in-process cosine scan over one org's skills can serve cheaply.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import TYPE_CHECKING, Any

from cachetools import LRUCache

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillFilter,
    SkillMatch,
    SkillMetadata,
    SkillStatus,
    matches_filter,
)
from app.agent_loop_lib.modules.providers.skills.index import SkillIndex
from app.agent_loop_lib.modules.providers.skills.text_scoring import (
    keyword_overlap_score,
    skill_haystack,
    tokenize,
)
from app.utils.env_utils import env_int

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

__all__ = ["SemanticSkillIndex"]

logger = logging.getLogger(__name__)


# Skill vectors are pure functions of the skill's text and the embedding model,
# so they are cached for the life of the process, shared across requests and
# orgs. `SkillManager` is built per request and calls `rebuild()` every time —
# without this, every chat re-embedded the org's whole skill catalog before the
# agent could start (measured at multiple seconds against a local embedder).
#
# The key IS the content, so there is no invalidation to get wrong: edit a
# skill and its text changes, so its hash changes and it is re-embedded. Same
# for switching embedding models. A stale entry is unreachable rather than
# wrong.
# `env_int(..., lo=1)`, not `int(os.getenv(...))`: a malformed value used to
# raise at import (taking down every importer), and 0 or negative built an
# LRUCache that raises "value too large" on the first write -- inside the
# embedding path, where the fallback is silent keyword scoring.
_VECTOR_CACHE_MAX = env_int("PIPESHUB_SKILL_VECTOR_CACHE_SIZE", 4096, lo=1)
_vector_cache: LRUCache = LRUCache(maxsize=_VECTOR_CACHE_MAX)


def _embedder_id(embedder: "Embeddings") -> str:
    """Stable-enough identity for the embedding model, so vectors from one
    model are never served to another (different space, often different
    dimensionality)."""
    for attr in ("model", "model_name", "model_id"):
        value = getattr(embedder, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(embedder).__name__


def _cache_key(embedder_id: str, text: str) -> str:
    return hashlib.sha256(f"{embedder_id}\x00{text}".encode()).hexdigest()


def _embedding_text(metadata: SkillMetadata) -> str:
    return f"{metadata.name}: {metadata.description} {' '.join(metadata.tags)} {' '.join(metadata.concepts)}"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticSkillIndex(SkillIndex):
    def __init__(self, retrieval_service: Any) -> None:
        self._retrieval_service = retrieval_service
        self._entries: dict[str, SkillMetadata] = {}
        self._vectors: dict[str, list[float]] = {}

    async def _embedder(self) -> "Embeddings | None":
        if self._retrieval_service is None:
            return None
        try:
            return await self._retrieval_service.get_embedding_model_instance()
        except Exception:
            logger.exception("SemanticSkillIndex: failed to resolve embedding model, falling back to keyword search")
            return None

    async def _embed_one(self, metadata: SkillMetadata) -> None:
        embedder = await self._embedder()
        if embedder is None:
            self._vectors.pop(metadata.name, None)
            return
        text = _embedding_text(metadata)
        key = _cache_key(_embedder_id(embedder), text)
        cached = _vector_cache.get(key)
        if cached is not None:
            self._vectors[metadata.name] = cached
            return
        try:
            vector = await embedder.aembed_query(text)
            self._vectors[metadata.name] = vector
            _vector_cache[key] = vector
        except Exception:
            logger.exception("SemanticSkillIndex: failed to embed skill %r", metadata.name)
            self._vectors.pop(metadata.name, None)

    # ---- SkillIndex -----------------------------------------------------

    async def rebuild(self, skills: list[SkillMetadata]) -> None:
        self._entries = {m.name: m for m in skills}
        embedder = await self._embedder()
        if embedder is None or not skills:
            self._vectors = {}
            return

        embedder_id = _embedder_id(embedder)
        texts = {m.name: _embedding_text(m) for m in skills}
        keys = {name: _cache_key(embedder_id, text) for name, text in texts.items()}

        vectors: dict[str, list[float]] = {}
        missing: list[SkillMetadata] = []
        for m in skills:
            cached = _vector_cache.get(keys[m.name])
            if cached is None:
                missing.append(m)
            else:
                vectors[m.name] = cached

        if missing:
            try:
                fresh = await embedder.aembed_documents([texts[m.name] for m in missing])
            except Exception:
                # Whatever is already cached still scores semantically; only the
                # uncached skills fall back to keyword matching.
                logger.exception(
                    "SemanticSkillIndex: bulk embedding failed for %d/%d skill(s), "
                    "those fall back to keyword search", len(missing), len(skills),
                )
                self._vectors = vectors
                return
            for m, vector in zip(missing, fresh):
                vectors[m.name] = vector
                _vector_cache[keys[m.name]] = vector

        logger.debug(
            "SemanticSkillIndex: rebuilt %d skill(s), %d embedded, %d from cache",
            len(skills), len(missing), len(skills) - len(missing),
        )
        self._vectors = vectors

    async def search(self, query: str, filter: SkillFilter | None = None, limit: int = 10) -> list[SkillMatch]:
        candidates = list(self._entries.values())
        if filter is not None:
            candidates = [m for m in candidates if matches_filter(m, filter)]
        if filter is None or filter.status is None:
            candidates = [m for m in candidates if m.status != SkillStatus.DEPRECATED]

        query = (query or "").strip()
        if not query:
            return [SkillMatch(skill=m, relevance=1.0, match_reason="catalog") for m in candidates[:limit]]

        query_tokens = tokenize(query)
        query_vector = await self._embed_query(query)

        scored: list[tuple[float, SkillMetadata, str]] = []
        for m in candidates:
            keyword_score, overlap = keyword_overlap_score(query_tokens, skill_haystack(m))
            semantic_score = 0.0
            if query_vector is not None:
                vector = self._vectors.get(m.name)
                if vector is not None:
                    semantic_score = _cosine_similarity(query_vector, vector)
            score = max(keyword_score, semantic_score)
            if score <= 0:
                continue
            reason = f"matched: {', '.join(sorted(overlap))}" if keyword_score >= semantic_score else "semantic match"
            scored.append((score, m, reason))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [SkillMatch(skill=m, relevance=score, match_reason=reason) for score, m, reason in scored[:limit]]

    async def _embed_query(self, query: str) -> list[float] | None:
        if not self._vectors:
            return None
        embedder = await self._embedder()
        if embedder is None:
            return None
        try:
            return await embedder.aembed_query(query)
        except Exception:
            logger.exception("SemanticSkillIndex: failed to embed query, falling back to keyword search")
            return None

    async def get_categories(self) -> dict[str, list[str]]:
        categories: dict[str, set[str]] = {}
        for m in self._entries.values():
            if m.category is None:
                continue
            categories.setdefault(m.category, set())
            if m.subcategory:
                categories[m.category].add(m.subcategory)
        return {category: sorted(subs) for category, subs in categories.items()}

    async def get_tags(self) -> list[str]:
        tags: set[str] = set()
        for m in self._entries.values():
            tags.update(m.tags)
        return sorted(tags)

    async def add_entry(self, metadata: SkillMetadata) -> None:
        self._entries[metadata.name] = metadata
        await self._embed_one(metadata)

    async def remove_entry(self, name: str) -> None:
        self._entries.pop(name, None)
        self._vectors.pop(name, None)

    async def update_entry(self, metadata: SkillMetadata) -> None:
        await self.add_entry(metadata)

from __future__ import annotations

import json
import logging
import os
import re

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillFilter,
    SkillMatch,
    SkillMetadata,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.index import SkillIndex

"""Filesystem `SkillIndex` — a JSON cache (`_meta/index.json`, primary root
only) of every skill's metadata, avoiding a re-parse of every SKILL.md on
every search. Rebuilt from disk by `SkillManager.start()`/`refresh()` (which
covers read-only external roots too — their entries are indexed here but
never written back to their own directory, only to the primary root's
`_meta/`).

Search is keyword-based (name + description + tags + category/subcategory,
token-overlap scoring) — sufficient for a local filesystem library; a
future `VectorSkillIndex` (embedding similarity) implements the same ABC
and swaps in via DI without any other module changing.
"""

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _matches_filter(metadata: SkillMetadata, filt: SkillFilter) -> bool:
    if filt.category is not None and metadata.category != filt.category:
        return False
    if filt.subcategory is not None and metadata.subcategory != filt.subcategory:
        return False
    if filt.status is not None and metadata.status != filt.status:
        return False
    if filt.source is not None and metadata.source != filt.source:
        return False
    if filt.tags and not (set(filt.tags) & set(metadata.tags)):
        return False
    return True


class FilesystemSkillIndex(SkillIndex):
    def __init__(self, primary_root: str, index_filename: str = "index.json") -> None:
        self._meta_dir = os.path.join(os.path.abspath(primary_root), "_meta")
        self._index_path = os.path.join(self._meta_dir, index_filename)
        self._entries: dict[str, SkillMetadata] = {}
        os.makedirs(self._meta_dir, exist_ok=True)
        self._load_cache()

    def _load_cache(self) -> None:
        if not os.path.isfile(self._index_path):
            return
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._entries = {name: SkillMetadata(**data) for name, data in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
            logger.warning("Discarding unreadable skill index at %s: %s", self._index_path, e)
            self._entries = {}

    def _save_cache(self) -> None:
        data = {name: metadata.model_dump(mode="json") for name, metadata in self._entries.items()}
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    async def rebuild(self, skills: list[SkillMetadata]) -> None:
        self._entries = {m.name: m for m in skills}
        self._save_cache()

    async def search(self, query: str, filter: SkillFilter | None = None, limit: int = 10) -> list[SkillMatch]:
        candidates = list(self._entries.values())
        if filter is not None:
            candidates = [m for m in candidates if _matches_filter(m, filter)]
        if filter is None or filter.status is None:
            # DEPRECATED skills are excluded from search by default — an
            # explicit `filter.status=DEPRECATED` opts back in.
            candidates = [m for m in candidates if m.status != SkillStatus.DEPRECATED]

        query = (query or "").strip()
        if not query:
            return [SkillMatch(skill=m, relevance=1.0, match_reason="catalog") for m in candidates[:limit]]

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scored: list[tuple[float, SkillMetadata, set[str]]] = []
        for m in candidates:
            haystack = _tokenize(
                f"{m.name} {m.description} {' '.join(m.tags)} {m.category or ''} {m.subcategory or ''}"
            )
            overlap = query_tokens & haystack
            if not overlap:
                continue
            scored.append((len(overlap) / len(query_tokens), m, overlap))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            SkillMatch(skill=m, relevance=score, match_reason=f"matched: {', '.join(sorted(overlap))}")
            for score, m, overlap in scored[:limit]
        ]

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
        self._save_cache()

    async def remove_entry(self, name: str) -> None:
        self._entries.pop(name, None)
        self._save_cache()

    async def update_entry(self, metadata: SkillMetadata) -> None:
        self._entries[metadata.name] = metadata
        self._save_cache()

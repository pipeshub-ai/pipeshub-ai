"""An in-memory stand-in for ``RetrievalService.search_lexical``."""

from __future__ import annotations

import re
from collections import defaultdict

from app.modules.retrieval.lexical_search import LexicalHit, LexicalSearchResult
from app.utils.exact_text_index import INDEX_CANDIDATE_CHUNKS

_WORD = re.compile(r"\w+")


class InvertedIndexSearcher:
    """Chunks holding any query word, *limit* per query, within the records
    each user may read (``readable``; everything when None).

    Built as a word -> chunks map, so a lookup costs the chunks that hold the
    word, as in a real lexical index.
    """

    def __init__(
        self, chunks: list[tuple[str, str]], readable: dict[str, set[str]] | None = None,
        *, store_chunks: int | None = 10_000_000,
    ) -> None:
        self.readable = readable
        self.store_chunks = store_chunks
        self.calls: list[dict] = []
        self._postings: dict[str, list[int]] = defaultdict(list)
        self._chunks = chunks
        for i, (_vrid, text) in enumerate(chunks):
            for word in dict.fromkeys(_WORD.findall(text.lower())):
                self._postings[word].append(i)

    async def lexical_search_available(self) -> bool:
        return True

    async def lexical_store_chunks(self, org_id: str, user_id: str) -> int | None:
        return self.store_chunks

    async def search_lexical(
        self, queries, user_id, org_id, filter_groups=None,
        limit=INDEX_CANDIDATE_CHUNKS, virtual_record_ids=None,
    ) -> LexicalSearchResult:
        self.calls.append({"queries": list(queries), "limit": limit, "virtual_record_ids": virtual_record_ids})
        allowed = None if self.readable is None else self.readable.get(user_id, set())
        scope = None if virtual_record_ids is None else set(virtual_record_ids)
        hits, capped = [], []
        for query in queries:
            matched = sorted({i for word in query.lower().split() for i in self._postings.get(word, ())})
            batch = [self._chunks[i] for i in matched if scope is None or self._chunks[i][0] in scope]
            capped.append(len(batch) >= limit)
            hits.append([
                LexicalHit(virtual_record_id=vrid, text=text, score=1.0)
                for vrid, text in batch[:limit] if allowed is None or vrid in allowed
            ])
        return LexicalSearchResult(hits=hits, capped=capped, empty_query=[False] * len(queries))

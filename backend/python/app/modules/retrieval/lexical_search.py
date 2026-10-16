"""Keyword lookups in the vector store's own lexical index.

Every provider already keeps one over chunk text: Qdrant the BM25 sparse
vectors, OpenSearch the analysed ``page_content`` field, Redis the
``page_content`` TEXT field. A lookup walks only the posting lists of its
terms, so its cost follows how many chunks hold them, not how many chunks
the store holds.
"""

from dataclasses import dataclass, field


class LexicalSearchUnavailableError(RuntimeError):
    """This deployment has no lexical index to look terms up in."""


@dataclass(frozen=True)
class LexicalHit:
    virtual_record_id: str
    text: str
    score: float


@dataclass
class LexicalSearchResult:
    """Per query, the chunks the user may read, best first.

    ``capped[i]`` says the store returned its whole limit for query ``i``
    before permission checks in some collection, so more chunks may hold its
    terms. ``empty_query[i]``
    says query ``i`` had no term the index keeps (stop words only), so its empty
    list proves nothing.
    """

    hits: list[list[LexicalHit]]
    capped: list[bool]
    empty_query: list[bool] = field(default_factory=list)
    # Collections that could not be searched, of how many were asked.
    failed_collections: int = 0
    searched_collections: int = 0

    @classmethod
    def nothing(cls, queries: int) -> "LexicalSearchResult":
        return cls(hits=[[] for _ in range(queries)], capped=[False] * queries, empty_query=[False] * queries)

"""What a reranker is to the rest of PipesHub: query and texts in, scored indices out.

Kept free of blocks, records and providers so any caller (search, entities,
a future record-level selection) can use any implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class RerankHit:
    index: int
    """Position of the document in the list passed to ``rerank``."""
    score: float
    """Relevance to the query. Comparable only within one call and one model."""


class RerankerError(Exception):
    """A reranker could not score the documents. The caller decides whether to fall back."""


class IReranker(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: int | None = None,
    ) -> list[RerankHit]:
        """Most relevant first, at most ``top_n`` hits (all when None).

        Returns an empty list for no documents. Raises ``RerankerError`` on
        any failure, never a provider-specific exception.
        """
        raise NotImplementedError

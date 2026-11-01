"""Second-stage record reranking (``RECORD_RERANK``), off unless enabled.

Importing the package registers the built-in strategies.
"""

# Imported for its side effect: it registers the built-in strategy.
from app.modules.retrieval.record_rerank import llm_listwise  # noqa: F401  # pyright: ignore[reportUnusedImport]
from app.modules.retrieval.record_rerank.base import (
    RecordCandidate,
    RecordReranker,
    RecordRerankError,
    RerankerDeps,
    RerankRequest,
)
from app.modules.retrieval.record_rerank.factory import (
    create_record_reranker,
    register_record_reranker,
    registered_strategies,
)
from app.modules.retrieval.record_rerank.pipeline import (
    HITS,
    POINTS,
    ItemView,
    RecordOrder,
    RecordRerank,
    RecordTexts,
    record_rerank_for,
    reorder_items,
    rerank_order,
    search_record_rerank,
    tool_search_record_rerank,
)
from app.modules.retrieval.record_rerank.settings import (
    RecordRerankSettings,
    record_rerank_enabled,
    record_rerank_settings,
)

__all__ = [
    "HITS",
    "POINTS",
    "ItemView",
    "RecordCandidate",
    "RecordOrder",
    "RecordRerank",
    "RecordRerankError",
    "RecordRerankSettings",
    "RecordReranker",
    "RecordTexts",
    "RerankRequest",
    "RerankerDeps",
    "create_record_reranker",
    "record_rerank_enabled",
    "record_rerank_for",
    "record_rerank_settings",
    "register_record_reranker",
    "registered_strategies",
    "reorder_items",
    "rerank_order",
    "search_record_rerank",
    "tool_search_record_rerank",
]

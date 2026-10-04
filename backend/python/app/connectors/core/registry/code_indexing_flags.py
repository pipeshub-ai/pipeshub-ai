"""Indexing-filter reads shared by the code-repository connectors."""
from __future__ import annotations

from app.connectors.core.registry.filters import FilterCollection, IndexingFilterKey


def code_indexing_flags(indexing_filters: FilterCollection | None) -> tuple[bool, bool]:
    """``(code_files_enabled, test_files_enabled)`` for a connector.

    Code files are on unless switched off. Test files are off unless opted in,
    even when the filters object exists but has no ``test_files`` row: a
    connector configured before that filter existed must not start indexing
    tests just because its config has no row for them.
    """
    if not indexing_filters:
        return True, False
    return (
        indexing_filters.is_enabled(IndexingFilterKey.CODE_FILES),
        indexing_filters.is_enabled(IndexingFilterKey.TEST_FILES, default=False),
    )

"""A VectorStore with everything mocked to bypass the constructor's side effects."""

from __future__ import annotations

from unittest.mock import AsyncMock

from tests.support.vector_db import (
    make_collection_registry as _make_collection_registry,
)


def make_vectorstore(supports_sparse: bool = False) -> VectorStore:  # noqa: F821 - imported inside, to keep import time off
    """Instantiate a VectorStore with everything mocked to bypass __init__ side effects."""
    from unittest.mock import MagicMock as _MM

    from app.modules.transformers.vectorstore import VectorStore
    from app.services.vector_db.models import VectorDBCapabilities

    vdb = AsyncMock()
    caps = VectorDBCapabilities(
        supports_sparse_vectors=supports_sparse,
        supports_server_side_text_search=False,
    )
    vdb.get_capabilities = _MM(return_value=caps)
    vdb.get_service_name = _MM(return_value="mock")

    return VectorStore(
        logger=_MM(),
        config_service=AsyncMock(),
        graph_provider=AsyncMock(),
        collection_registry=_make_collection_registry(),
        vector_db_service=vdb,
    )

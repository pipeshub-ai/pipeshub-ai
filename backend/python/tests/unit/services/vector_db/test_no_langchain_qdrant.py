"""Guard: production modules must not import langchain_qdrant."""

import importlib
import sys
from unittest.mock import MagicMock


def test_no_langchain_qdrant_imports_in_vector_modules():
    blocked = MagicMock()
    sys.modules["langchain_qdrant"] = blocked

    for module_name in (
        "app.modules.retrieval.retrieval_service",
        "app.modules.transformers.vectorstore",
        "app.modules.indexing.run",
    ):
        sys.modules.pop(module_name, None)
        mod = importlib.import_module(module_name)
        assert mod is not None

    # SparseEmbedder is the supported path
    from app.services.vector_db import sparse_embeddings

    assert sparse_embeddings.SparseEmbedder is not None

    sys.modules.pop("langchain_qdrant", None)

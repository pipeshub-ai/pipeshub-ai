"""Guard: production modules must not import langchain_qdrant."""

import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

_ABSENT = ModuleType("absent")  # stands for a module that was not imported yet: undoing the patch removes the entry
_RELOADED = (
    "app.modules.retrieval.retrieval_service",
    "app.modules.transformers.vectorstore",
    "app.modules.indexing.run",
)


def test_no_langchain_qdrant_imports_in_vector_modules(monkeypatch):
    # Importing the modules again replaces their entries in sys.modules, and the attribute their package keeps; a later test that
    # imported one of them at collection holds the old module while the rest of the process finds the new one. Both are put back.
    for name in _RELOADED:
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name, _ABSENT))
        parent, _, child = name.rpartition(".")
        if parent in sys.modules and hasattr(sys.modules[parent], child):
            monkeypatch.setattr(sys.modules[parent], child, getattr(sys.modules[parent], child))
    blocked = MagicMock()
    monkeypatch.setitem(sys.modules, "langchain_qdrant", blocked)

    for module_name in _RELOADED:
        sys.modules.pop(module_name, None)
        mod = importlib.import_module(module_name)
        assert mod is not None

    # SparseEmbedder is the supported path
    from app.services.vector_db import sparse_embeddings

    assert sparse_embeddings.SparseEmbedder is not None


def test_the_guard_puts_back_the_modules_it_imports_again() -> None:
    """A test that imported one of them at collection must still be talking to the module the rest of the process finds."""
    importlib.import_module("app.modules.retrieval.retrieval_service")
    before = {name: sys.modules[name] for name in _RELOADED if name in sys.modules}
    with pytest.MonkeyPatch.context() as patched:
        test_no_langchain_qdrant_imports_in_vector_modules(patched)
    assert {name: sys.modules[name] for name in before} == before
    parent = sys.modules["app.modules.retrieval"]
    assert parent.retrieval_service is before["app.modules.retrieval.retrieval_service"]
